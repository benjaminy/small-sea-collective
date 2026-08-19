"""Cod Sync: publishing and fetching a `main` history through a bundle store.

This module coordinates. It executes no Git command and parses no YAML: repo.py
owns the repository, store.py owns byte transport, and format.py owns the link
codec. What is left here is the decision-making, which is entirely about what
must be proved before anything durable moves.

What Cod Sync proves
    A bundle advertises the head its link declares, the bundle's actual Git
    prerequisites are covered by the predecessor its link declares, and the
    resulting commit graph has the promised ancestry.

What Cod Sync does not prove
    Authorship, membership, complete disclosure, or that a fetched history
    should have any local effect. Fetching stays separate from merging:
    `fetch` moves no ref except an explicitly requested forward-only pin.
"""

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from cod_sync.format import (
    COD_SYNC_VERSION,
    BundleDescriptor,
    Link,
    Predecessor,
    decode_link,
    encode_link,
    new_uid,
    parse_version,
    signed_link,
)
from cod_sync.repo import RefDivergedError, Repo
from cod_sync.store import ObjectNotFoundError

logger = logging.getLogger("cod_sync")

#: The one ref Cod Sync transports.
MAIN_REF = "refs/heads/main"


class CodSyncError(Exception):
    """Base class for coordinator failures."""


class NoPublishedHeadError(CodSyncError):
    """The store holds no chain, so there is nothing to fetch."""


class NoLocalHeadError(CodSyncError):
    """The local repository has no `main` to publish."""


class ChainError(CodSyncError):
    """The store's chain is malformed, inconsistent, or contradicts a bundle.

    Carries whatever identifiers the operation had reached, because a rejected
    chain is only actionable if the report says which objects disagreed.
    """

    def __init__(
        self,
        message: str,
        link_uid: Optional[str] = None,
        bundle_uid: Optional[str] = None,
        declared_head: Optional[str] = None,
        advertised_head: Optional[str] = None,
        declared_prerequisites: Optional[Set[str]] = None,
        actual_prerequisites: Optional[Set[str]] = None,
    ):
        details = [
            f"link={link_uid}" if link_uid else None,
            f"bundle={bundle_uid}" if bundle_uid else None,
            f"declared_head={declared_head}" if declared_head else None,
            f"advertised_head={advertised_head}" if advertised_head else None,
            (
                f"declared_prerequisites={sorted(declared_prerequisites)}"
                if declared_prerequisites is not None
                else None
            ),
            (
                f"actual_prerequisites={sorted(actual_prerequisites)}"
                if actual_prerequisites is not None
                else None
            ),
        ]
        suffix = ", ".join(d for d in details if d)
        super().__init__(f"{message} ({suffix})" if suffix else message)
        self.link_uid = link_uid
        self.bundle_uid = bundle_uid
        self.declared_head = declared_head
        self.advertised_head = advertised_head
        self.declared_prerequisites = declared_prerequisites
        self.actual_prerequisites = actual_prerequisites


class PublicationIntegrationRequiredError(CodSyncError):
    """The store's head is not an ancestor of local `main`.

    Cod Sync cannot choose the integration: it may be running against a
    repository with no work tree, and dropping the stored head would discard
    another device's or teammate's commits. Nothing is uploaded and the store's
    head is untouched. A caller with a work tree and a policy decides what to
    do next.
    """

    def __init__(
        self,
        message: str,
        stored_head: str,
        local_head: str,
        link_uid: str,
        merge_base: Optional[str] = None,
    ):
        super().__init__(
            f"{message} (stored_head={stored_head}, local_head={local_head}, "
            f"link={link_uid}, merge_base={merge_base})"
        )
        self.stored_head = stored_head
        self.local_head = local_head
        self.link_uid = link_uid
        self.merge_base = merge_base


class PinIntegrationRequiredError(CodSyncError):
    """The requested pin has diverged from the head just fetched.

    The pin is left exactly where it was.
    """

    def __init__(self, ref_name: str, current_sha: str, observed_head: str, link_uid: str):
        super().__init__(
            f"{ref_name} at {current_sha} has diverged from fetched head "
            f"{observed_head} (link={link_uid})"
        )
        self.ref_name = ref_name
        self.current_sha = current_sha
        self.observed_head = observed_head
        self.link_uid = link_uid


@dataclass(frozen=True)
class PublishResult:
    """The outcome of a publication.

    changed=False is an ordinary success: the store already held this head, so
    nothing was uploaded and no notification was sent.
    """

    head: str
    changed: bool
    link_uid: str


@dataclass(frozen=True)
class FetchResult:
    """The outcome of a fetch.

    observed_head is the validated head the store publishes. pinned_head is
    where the requested pin ended up, which is not always observed_head: an
    out-of-order fetch whose observation is older than the pin reports
    disposition "stale" and leaves the newer pin alone.
    """

    observed_head: str
    link_uid: str
    pinned_head: Optional[str] = None
    pin_disposition: Optional[str] = None
    links_read: int = 0
    bundles_downloaded: int = 0


@dataclass(frozen=True)
class _ChainEntry:
    """A link and the local bundle path holding its validated contents."""

    link: Link
    bundle_path: Path
    descriptor: BundleDescriptor


class CodSync:
    """Publish and fetch one repository's `main` through one store."""

    def __init__(self, repo: Repo, store):
        self.repo = repo
        self.store = store

    # ------------------------------------------------------------------ #
    # Publication
    # ------------------------------------------------------------------ #

    def publish(
        self, signing_key=None, teammate_id=None, device_public_key=None
    ) -> PublishResult:
        """Extend the store's chain with local `main`.

        An empty store gets a full snapshot under a create-only write. A store
        whose head equals local `main` is left alone. Any other store head must
        exist locally and be an ancestor of local `main`, or publication stops
        before uploading anything.
        """
        local_head = self.repo.resolve_ref(MAIN_REF)
        if local_head is None:
            raise NoLocalHeadError(f"{MAIN_REF} does not resolve; nothing to publish")

        try:
            latest_bytes, etag = self.store.get_latest_link()
        except ObjectNotFoundError:
            latest_bytes, etag = None, None

        with tempfile.TemporaryDirectory(prefix="cod-sync-publish-") as work_dir:
            work = Path(work_dir)
            if latest_bytes is None:
                return self._publish_initial(
                    local_head, work, signing_key, teammate_id, device_public_key
                )
            return self._publish_onto(
                latest_bytes,
                etag,
                local_head,
                work,
                signing_key,
                teammate_id,
                device_public_key,
            )

    def _publish_initial(
        self, local_head, work: Path, signing_key, teammate_id, device_public_key
    ) -> PublishResult:
        link = Link(
            link_id=new_uid(),
            head=local_head,
            bundle_id=new_uid(),
            previous=None,
        )
        bundle_path = work / f"{link.bundle_id}.bundle"
        self.repo.create_bundle(bundle_path, [MAIN_REF])
        self._require_bundle_matches(link, bundle_path)
        self._upload(link, bundle_path, expected_etag=None,
                     signing_key=signing_key, teammate_id=teammate_id,
                     device_public_key=device_public_key)
        return PublishResult(head=local_head, changed=True, link_uid=link.link_id)

    def _publish_onto(
        self,
        latest_bytes: bytes,
        etag: Optional[str],
        local_head: str,
        work: Path,
        signing_key,
        teammate_id,
        device_public_key,
    ) -> PublishResult:
        stored = decode_link(latest_bytes)
        if parse_version(COD_SYNC_VERSION) < stored.version_tuple:
            raise ChainError(
                f"the store's head is version {stored.version} and this writer "
                f"would regress it to {COD_SYNC_VERSION}",
                link_uid=stored.link_id,
            )

        # The archived copy is what a successor's predecessor pointer resolves
        # to, so a chain must not be extended through a link nobody can reread.
        try:
            archived = self.store.get_link(stored.link_id)
        except ObjectNotFoundError as exc:
            raise ChainError(
                "the store's head has no archived copy to point back at",
                link_uid=stored.link_id,
            ) from exc
        if archived != latest_bytes:
            raise ChainError(
                "the store's head and its archived copy differ",
                link_uid=stored.link_id,
            )

        stored_bundle = work / f"{stored.bundle_id}.bundle"
        self.store.download_bundle(stored.bundle_id, stored_bundle)
        self._require_bundle_matches(stored, stored_bundle)
        self._verify_stored_bundle(stored, stored_bundle)

        if stored.head == local_head:
            return PublishResult(
                head=local_head, changed=False, link_uid=stored.link_id
            )

        if not self.repo.has_commit(stored.head):
            raise PublicationIntegrationRequiredError(
                "the store's head is not present locally",
                stored_head=stored.head,
                local_head=local_head,
                link_uid=stored.link_id,
            )
        if not self.repo.is_ancestor(stored.head, MAIN_REF):
            raise PublicationIntegrationRequiredError(
                "the store's head is not an ancestor of local main",
                stored_head=stored.head,
                local_head=local_head,
                link_uid=stored.link_id,
                merge_base=self.repo.merge_base(stored.head, local_head),
            )

        link = Link(
            link_id=new_uid(),
            head=local_head,
            bundle_id=new_uid(),
            previous=Predecessor(link_id=stored.link_id, head=stored.head),
        )
        bundle_path = work / f"{link.bundle_id}.bundle"
        self.repo.create_bundle(bundle_path, [f"^{stored.head}", MAIN_REF])
        self._require_bundle_matches(link, bundle_path)
        self._upload(link, bundle_path, expected_etag=etag,
                     signing_key=signing_key, teammate_id=teammate_id,
                     device_public_key=device_public_key)
        return PublishResult(head=local_head, changed=True, link_uid=link.link_id)

    def _upload(
        self, link: Link, bundle_path: Path, expected_etag: Optional[str],
        signing_key, teammate_id, device_public_key,
    ):
        """Write the bundle, then the archived link, then the head.

        The head write is the serialization point, so it goes last: a
        publication that loses the race leaves only unreferenced write-once
        objects rather than a chain pointing at something nobody uploaded.
        """
        if signing_key is not None and teammate_id is not None:
            link = signed_link(link, signing_key, teammate_id, device_public_key)
        blob = encode_link(link)
        self.store.put_bundle(link.bundle_id, bundle_path)
        self.store.put_link(link.link_id, blob)
        self.store.put_latest_link(blob, expected_etag, link_uid=link.link_id)

    # ------------------------------------------------------------------ #
    # Fetch
    # ------------------------------------------------------------------ #

    def fetch(self, pin_to_ref: Optional[str] = None) -> FetchResult:
        """Import the store's published `main` history.

        Creates no remote, remote-tracking ref, FETCH_HEAD, or temporary tag.
        The only durable ref that can move is pin_to_ref, and only forward.
        """
        try:
            latest_bytes, _etag = self.store.get_latest_link()
        except ObjectNotFoundError as exc:
            raise NoPublishedHeadError("the store publishes no head") from exc

        latest = decode_link(latest_bytes)

        with tempfile.TemporaryDirectory(prefix="cod-sync-fetch-") as work_dir:
            work = Path(work_dir)
            chain, links_read, downloads = self._resolve(latest, work)
            for entry in chain:
                self._import(entry)
            observed_head = latest.head

        pinned_head = None
        disposition = None
        if pin_to_ref is not None:
            try:
                advance = self.repo.advance_ref(pin_to_ref, observed_head)
            except RefDivergedError as exc:
                raise PinIntegrationRequiredError(
                    pin_to_ref, exc.current_sha, observed_head, latest.link_id
                ) from exc
            pinned_head = advance.current_sha
            disposition = advance.disposition

        return FetchResult(
            observed_head=observed_head,
            link_uid=latest.link_id,
            pinned_head=pinned_head,
            pin_disposition=disposition,
            links_read=links_read,
            bundles_downloaded=downloads,
        )

    def _resolve(self, latest: Link, work: Path):
        """Walk newest to oldest until the chain reaches local history.

        Returns the entries still needing import, oldest first, along with how
        many links were read and how many bundles were downloaded. Every bundle
        is downloaded once, to its own path.
        """
        entry = self._download_and_check(latest, work)
        links_read = 1
        downloads = 1

        # A head already present locally still has to be validated as a
        # publication, but once it is, there is nothing left to walk or import.
        if self._already_satisfied(entry):
            return [], links_read, downloads

        pending: List[_ChainEntry] = [entry]
        visited = {latest.link_id}
        current = latest
        while current.previous is not None and not self.repo.has_commit(
            current.previous.head
        ):
            previous_uid = current.previous.link_id
            if previous_uid in visited:
                raise ChainError(
                    "the chain revisits a link it already read",
                    link_uid=previous_uid,
                )
            visited.add(previous_uid)
            try:
                previous_bytes = self.store.get_link(previous_uid)
            except ObjectNotFoundError as exc:
                raise ChainError(
                    "the chain names a predecessor the store does not hold",
                    link_uid=previous_uid,
                ) from exc
            previous = decode_link(previous_bytes)
            links_read += 1
            self._require_predecessor_consistent(current, previous)
            pending.append(self._download_and_check(previous, work))
            downloads += 1
            current = previous

        pending.reverse()
        return pending, links_read, downloads

    def _download_and_check(self, link: Link, work: Path) -> _ChainEntry:
        bundle_path = work / f"{link.bundle_id}.bundle"
        self.store.download_bundle(link.bundle_id, bundle_path)
        descriptor = self._require_bundle_matches(link, bundle_path)
        return _ChainEntry(link=link, bundle_path=bundle_path, descriptor=descriptor)

    def _already_satisfied(self, entry: _ChainEntry) -> bool:
        """True when the validated latest bundle needs no import."""
        link = entry.link
        if not self.repo.has_commit(link.head):
            return False
        if any(
            not self.repo.has_commit(sha) for sha in entry.descriptor.prerequisites
        ):
            return False
        self.repo.verify_bundle(entry.bundle_path)
        self._require_ancestry(link, entry.descriptor)
        return True

    def _import(self, entry: _ChainEntry):
        link = entry.link
        # The walk stops at the declared predecessor, so a bundle that needs
        # anything outside that history arrives here unsatisfiable. Say so,
        # rather than letting `bundle verify` report it as a bare git failure.
        missing = {
            sha for sha in entry.descriptor.prerequisites if not self.repo.has_commit(sha)
        }
        if missing:
            raise ChainError(
                "the bundle needs prerequisites the chain never published",
                link_uid=link.link_id,
                bundle_uid=link.bundle_id,
                declared_head=link.head,
                declared_prerequisites=(
                    set() if link.previous is None else {link.previous.head}
                ),
                actual_prerequisites=set(entry.descriptor.prerequisites),
            )
        self.repo.verify_bundle(entry.bundle_path)
        self.repo.import_bundle(entry.bundle_path)
        if not self.repo.has_commit(link.head):
            raise ChainError(
                "importing the bundle did not produce its declared head",
                link_uid=link.link_id,
                bundle_uid=link.bundle_id,
                declared_head=link.head,
            )
        self._require_ancestry(link, entry.descriptor)

    # ------------------------------------------------------------------ #
    # Shared validation
    # ------------------------------------------------------------------ #

    def _require_bundle_matches(self, link: Link, bundle_path) -> BundleDescriptor:
        """Check a bundle's own header against the link that advertises it."""
        heads: Dict[str, str] = self.repo.bundle_heads(bundle_path)
        prerequisites = frozenset(self.repo.bundle_prerequisites(bundle_path))
        descriptor = BundleDescriptor(
            head=heads.get(MAIN_REF), prerequisites=prerequisites
        )

        if set(heads) != {MAIN_REF}:
            raise ChainError(
                f"the bundle advertises {sorted(heads)} instead of only {MAIN_REF}",
                link_uid=link.link_id,
                bundle_uid=link.bundle_id,
                declared_head=link.head,
            )
        if descriptor.head != link.head:
            raise ChainError(
                "the bundle advertises a different head than its link declares",
                link_uid=link.link_id,
                bundle_uid=link.bundle_id,
                declared_head=link.head,
                advertised_head=descriptor.head,
            )
        declared = set() if link.previous is None else {link.previous.head}
        # An initial bundle is a full snapshot, so any prerequisite at all
        # means it silently depends on history nobody published.
        if link.previous is None:
            if prerequisites:
                raise ChainError(
                    "an initial bundle declares no predecessor but needs prerequisites",
                    link_uid=link.link_id,
                    bundle_uid=link.bundle_id,
                    declared_head=link.head,
                    declared_prerequisites=declared,
                    actual_prerequisites=set(prerequisites),
                )
        elif link.previous.head not in prerequisites:
            raise ChainError(
                "the bundle does not build on the predecessor its link declares",
                link_uid=link.link_id,
                bundle_uid=link.bundle_id,
                declared_head=link.head,
                declared_prerequisites=declared,
                actual_prerequisites=set(prerequisites),
            )
        return descriptor

    def _require_ancestry(self, link: Link, descriptor: BundleDescriptor):
        """Check the graph claims that only hold once the objects are present.

        A merge pulls in commits whose parents sit behind the predecessor, so
        git legitimately records more than one prerequisite. Extra ones are
        acceptable exactly when the declared predecessor already contains them:
        then anyone who can satisfy the declared prerequisite can satisfy the
        bundle. An extra outside that history is a hidden dependency.
        """
        if link.previous is None:
            return
        for sha in descriptor.prerequisites - {link.previous.head}:
            if not self.repo.is_ancestor(sha, link.previous.head):
                raise ChainError(
                    "the bundle needs a prerequisite outside its declared predecessor",
                    link_uid=link.link_id,
                    bundle_uid=link.bundle_id,
                    declared_head=link.head,
                    declared_prerequisites={link.previous.head},
                    actual_prerequisites=set(descriptor.prerequisites),
                )
        if self.repo.has_commit(link.head) and not self.repo.is_ancestor(
            link.previous.head, link.head
        ):
            raise ChainError(
                "the declared head does not descend from its declared prerequisite",
                link_uid=link.link_id,
                bundle_uid=link.bundle_id,
                declared_head=link.head,
                declared_prerequisites={link.previous.head},
            )

    def _verify_stored_bundle(self, link: Link, bundle_path):
        """Verify a stored publication before reporting success or extending it."""
        descriptor = BundleDescriptor(
            head=link.head,
            prerequisites=frozenset(self.repo.bundle_prerequisites(bundle_path)),
        )
        if any(not self.repo.has_commit(sha) for sha in descriptor.prerequisites):
            raise ChainError(
                "the stored bundle has unavailable prerequisites",
                link_uid=link.link_id,
                bundle_uid=link.bundle_id,
                declared_head=link.head,
                declared_prerequisites=(
                    set() if link.previous is None else {link.previous.head}
                ),
                actual_prerequisites=set(descriptor.prerequisites),
            )
        self.repo.verify_bundle(bundle_path)
        self._require_ancestry(link, descriptor)

    @staticmethod
    def _require_predecessor_consistent(child: Link, parent: Link):
        """Check an archived link against the pointer that led to it."""
        expected = child.previous
        if parent.link_id != expected.link_id:
            raise ChainError(
                "the archived link's own id does not match the id it was read under",
                link_uid=expected.link_id,
            )
        if parent.head != expected.head:
            raise ChainError(
                "the predecessor publishes a different head than its successor claims",
                link_uid=parent.link_id,
                declared_head=expected.head,
                advertised_head=parent.head,
            )
        if parent.version_tuple > child.version_tuple:
            raise ChainError(
                f"the chain regresses from version {parent.version} to {child.version}",
                link_uid=parent.link_id,
            )
