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

What a publication may move
    `publish` may import validated objects and may create a ref under
    PARKED_REF_PREFIX for a competing head it observed. It never moves `main`,
    touches a work tree, or constructs a merge.
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
from cod_sync.store import CasConflictError, ObjectNotFoundError, StoreError

logger = logging.getLogger("cod_sync")

#: The one ref Cod Sync transports.
MAIN_REF = "refs/heads/main"

#: Namespace Cod Sync owns for competing heads it observed but did not adopt.
PARKED_REF_PREFIX = "refs/cod-sync/parked"

#: The three writes of one publication, in the order _upload performs them.
PHASE_BUNDLE = "bundle"
PHASE_ARCHIVED_LINK = "archived_link"
PHASE_HEAD = "head"


def parked_ref_name(link_uid: str) -> str:
    return f"{PARKED_REF_PREFIX}/{link_uid}"


def _comparable(etag: Optional[str]) -> bool:
    """True when the store returned an etag it can be held to.

    None and "" are both incomparable: the Google Drive and Dropbox adapters
    substitute "" when the provider reports no validator, so testing only
    `is not None` would read a missing etag as evidence.
    """
    return bool(etag)


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


class PublicationFailedError(CodSyncError):
    """A publication invocation that did not finish unattended.

    The three subclasses are the three terminal meanings a caller must tell
    apart. They share one field set because every one of them reports the same
    kinds of evidence — which head this invocation tried to publish, which
    stored head it built on, which stored head an observation pass produced,
    and what happened to the head write — and only some of those apply to any
    one outcome. Callers read the fields; nobody parses the message.

    attempted_head is local `main` frozen at the start of the invocation.
    predecessor_head is the validated stored head this invocation built its
    candidate on, absent when it built none. observed_head is the validated
    stored head the last observation pass produced, absent when that pass
    confirmed exact absence or failed.
    """

    disposition = ""

    def __init__(
        self,
        message: str,
        *,
        attempted_head: str,
        attempted_link_uid: Optional[str] = None,
        predecessor_head: Optional[str] = None,
        predecessor_etag: Optional[str] = None,
        observed_head: Optional[str] = None,
        observed_etag: Optional[str] = None,
        observed_link_uid: Optional[str] = None,
        observed_absent: bool = False,
        write_phase: Optional[str] = None,
        cause: Optional[Exception] = None,
        observation_failure: Optional[Exception] = None,
        merge_base: Optional[str] = None,
        parked_ref: Optional[str] = None,
        imported: bool = False,
    ):
        details = [
            f"disposition={self.disposition}",
            f"attempted_head={attempted_head}",
            f"predecessor_head={predecessor_head}" if predecessor_head else None,
            f"observed_head={observed_head}" if observed_head else None,
            "observed=absent" if observed_absent else None,
            f"write_phase={write_phase}" if write_phase else None,
            f"cause={cause!r}" if cause is not None else None,
            (
                f"observation_failure={observation_failure!r}"
                if observation_failure is not None
                else None
            ),
            f"merge_base={merge_base}" if merge_base else None,
            f"parked_ref={parked_ref}" if parked_ref else None,
        ]
        super().__init__(f"{message} ({', '.join(d for d in details if d)})")
        self.attempted_head = attempted_head
        self.attempted_link_uid = attempted_link_uid
        self.predecessor_head = predecessor_head
        self.predecessor_etag = predecessor_etag
        self.observed_head = observed_head
        self.observed_etag = observed_etag
        self.observed_link_uid = observed_link_uid
        self.observed_absent = observed_absent
        self.write_phase = write_phase
        self.cause = cause
        self.observation_failure = observation_failure
        self.merge_base = merge_base
        self.parked_ref = parked_ref
        self.imported = imported


class PublicationRetryableError(PublicationFailedError):
    """This invocation's head write was never issued or is proven closed.

    Nothing observed requires application integration first, so a later
    invocation may publish again. Before the head write, write_phase names a
    failed object upload; an initial observation failure has no write phase and
    appears in observation_failure. After a failed head write, the write is
    closed, either because the failure was conclusive or because an observation
    spent its condition — and if that observation itself failed, this says no
    divergence was observed, not that none exists.
    """

    disposition = "retryable"


class PublicationIntegrationRequiredError(PublicationFailedError):
    """The validated stored head and attempted_head diverge, with no open write.

    Cod Sync cannot choose the integration: it may be running against a
    repository with no work tree, and dropping the stored head would discard
    another device's or teammate's commits. The observed head is preserved at
    parked_ref for the application operation that integrates it.
    """

    disposition = "integration_required"


class PublicationOutcomeUnresolvedError(PublicationFailedError):
    """This invocation's head write is still open.

    It may yet move the stored head, whatever Git state the settlement pass
    observed, so no caller may record the publication as settled. Divergence
    and containment seen by that pass ride along as evidence rather than
    overriding the open write.
    """

    disposition = "outcome_unresolved"


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
    """A publication that finished without needing anyone's attention.

    disposition "published" means this invocation's head write succeeded.
    "already_present" means the validated stored head equals or descends from
    attempted_head with no head write left open, so the attempted state is
    stored whether or not this invocation's own link is the one holding it.
    Callers that care which of the two happened compare observed_head.
    """

    disposition: str
    attempted_head: str
    observed_head: str
    observed_link_uid: str
    observed_etag: Optional[str] = None
    predecessor_head: Optional[str] = None
    predecessor_etag: Optional[str] = None
    attempted_link_uid: Optional[str] = None


@dataclass(frozen=True)
class _Observation:
    """One validated read of the stored head.

    head None means the store confirmed the chain does not exist. A pass that
    failed to establish the head produces no observation at all.
    """

    head: Optional[str] = None
    etag: Optional[str] = None
    link_uid: Optional[str] = None
    imported: bool = False


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

        The invocation gets a fixed envelope: at most one head write and at
        most two validated observation passes. The first pass runs before any
        upload and can end the invocation on its own — an equal or covering
        stored head is already_present, a divergent one is parked and reported.
        A failed head write buys the second pass, which settles two separate
        questions: what the stored history now holds, and whether this
        invocation's write can still take effect. Nothing is retried here; a
        later invocation observes afresh.

        Returns a PublishResult for the two ordinary outcomes and raises a
        PublicationFailedError subclass for the three that need attention.
        """
        attempted_head = self.repo.resolve_ref(MAIN_REF)
        if attempted_head is None:
            raise NoLocalHeadError(f"{MAIN_REF} does not resolve; nothing to publish")

        with tempfile.TemporaryDirectory(prefix="cod-sync-publish-") as work_dir:
            work = Path(work_dir)
            try:
                observed = self._observe(work / "initial")
            except StoreError as exc:
                raise PublicationRetryableError(
                    "the initial observation failed before any head write",
                    attempted_head=attempted_head,
                    observation_failure=exc,
                ) from exc

            if observed.head is None:
                link = Link(
                    link_id=new_uid(),
                    head=attempted_head,
                    bundle_id=new_uid(),
                    previous=None,
                )
            else:
                state = self._git_state(observed.head, attempted_head)
                if state == "contains":
                    # No head write has been attempted, so none can be open.
                    return PublishResult(
                        disposition="already_present",
                        attempted_head=attempted_head,
                        observed_head=observed.head,
                        observed_link_uid=observed.link_uid,
                        observed_etag=observed.etag,
                    )
                if state == "diverged":
                    raise PublicationIntegrationRequiredError(
                        "the store's head diverges from local main",
                        attempted_head=attempted_head,
                        observed_head=observed.head,
                        observed_etag=observed.etag,
                        observed_link_uid=observed.link_uid,
                        merge_base=self.repo.merge_base(observed.head, attempted_head),
                        parked_ref=self._park(observed),
                        imported=observed.imported,
                    )
                # A successor has to be written conditionally on this exact
                # head, and settlement has to read the etag as evidence. A
                # store that supplies no comparable etag can do neither, which
                # is a broken store rather than a publication outcome.
                if not _comparable(observed.etag):
                    raise ChainError(
                        "the store's head arrived without a comparable etag, so it "
                        "supports neither a conditional head write nor settlement",
                        link_uid=observed.link_uid,
                        declared_head=observed.head,
                    )
                link = Link(
                    link_id=new_uid(),
                    head=attempted_head,
                    bundle_id=new_uid(),
                    previous=Predecessor(
                        link_id=observed.link_uid, head=observed.head
                    ),
                )

            bundle_path = work / f"{link.bundle_id}.bundle"
            self.repo.create_bundle_from_head(
                bundle_path,
                attempted_head,
                predecessor_head=observed.head,
            )
            self._require_bundle_matches(link, bundle_path)
            return self._upload(
                link,
                bundle_path,
                attempted_head,
                observed,
                work,
                signing_key,
                teammate_id,
                device_public_key,
            )

    # -- observation -- #

    def _observe(self, work: Path) -> _Observation:
        """Read the stored head once and validate it as a publication.

        Importing is part of observing: comparing an observed head to
        attempted_head, computing a merge base, and parking a ref all need the
        commit locally, and a stored bundle is verified but never imported by
        its header check alone. So a head that is not already present is
        fetched through the ordinary chain walk. That imports validated objects
        and nothing else: `main`, the work tree, and every application ref are
        left exactly where they were.
        """
        try:
            latest_bytes, etag = self.store.get_latest_link()
        except ObjectNotFoundError:
            return _Observation()

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

        work.mkdir(parents=True, exist_ok=True)
        imported = False
        if self.repo.has_commit(stored.head):
            stored_bundle = work / f"{stored.bundle_id}.bundle"
            self.store.download_bundle(stored.bundle_id, stored_bundle)
            self._require_bundle_matches(stored, stored_bundle)
            self._verify_stored_bundle(stored, stored_bundle)
        else:
            pending, _links_read, _downloads = self._resolve(stored, work)
            for entry in pending:
                self._import(entry)
            imported = True

        return _Observation(
            head=stored.head,
            etag=etag,
            link_uid=stored.link_id,
            imported=imported,
        )

    def _git_state(self, observed_head: str, attempted_head: str) -> str:
        """Where a validated stored head sits relative to the attempted one."""
        if observed_head == attempted_head or self.repo.is_ancestor(
            attempted_head, observed_head
        ):
            return "contains"
        if self.repo.is_ancestor(observed_head, attempted_head):
            return "behind"
        return "diverged"

    def _park(self, observed: _Observation) -> str:
        """Preserve a competing head for a later, separate integration process.

        The publishing process exits with only an exception to show for the
        conflict, so the head it observed has to survive as a ref or the
        recovery path would have to refetch and re-decide. One ref per observed
        link, never advanced, so a second conflict cannot replace the only
        record of the first.
        """
        ref_name = parked_ref_name(observed.link_uid)
        existing = self.repo.resolve_ref(ref_name)
        if existing is None:
            self.repo.advance_ref(ref_name, observed.head)
        elif existing != observed.head:
            raise ChainError(
                "the same link has already been parked at a different head",
                link_uid=observed.link_uid,
                declared_head=observed.head,
                advertised_head=existing,
            )
        return ref_name

    # -- writing -- #

    def _upload(
        self,
        link: Link,
        bundle_path: Path,
        attempted_head: str,
        predecessor: _Observation,
        work: Path,
        signing_key,
        teammate_id,
        device_public_key,
    ) -> PublishResult:
        """Write the bundle, then the archived link, then the head.

        The head write is the serialization point, so it goes last: a
        publication that loses the race leaves only unreferenced write-once
        objects rather than a chain pointing at something nobody uploaded. That
        is also why a failure before the head write needs no settlement pass —
        the shared head cannot have moved, so the invocation is retryable with
        the phase that failed.
        """
        if signing_key is not None and teammate_id is not None:
            link = signed_link(link, signing_key, teammate_id, device_public_key)
        blob = encode_link(link)

        for phase, write in (
            (PHASE_BUNDLE, lambda: self.store.put_bundle(link.bundle_id, bundle_path)),
            (PHASE_ARCHIVED_LINK, lambda: self.store.put_link(link.link_id, blob)),
        ):
            try:
                write()
            except StoreError as exc:
                raise PublicationRetryableError(
                    "the publication failed before any head write",
                    attempted_head=attempted_head,
                    attempted_link_uid=link.link_id,
                    predecessor_head=predecessor.head,
                    predecessor_etag=predecessor.etag,
                    observed_head=predecessor.head,
                    observed_etag=predecessor.etag,
                    observed_link_uid=predecessor.link_uid,
                    observed_absent=predecessor.head is None,
                    write_phase=phase,
                    cause=exc,
                ) from exc

        try:
            new_etag = self.store.put_latest_link(
                blob, predecessor.etag, link_uid=link.link_id
            )
        except StoreError as exc:
            return self._settle(exc, link, attempted_head, predecessor, work)

        return PublishResult(
            disposition="published",
            attempted_head=attempted_head,
            observed_head=attempted_head,
            observed_link_uid=link.link_id,
            observed_etag=new_etag,
            predecessor_head=predecessor.head,
            predecessor_etag=predecessor.etag,
            attempted_link_uid=link.link_id,
        )

    # -- settlement -- #

    def _settle(
        self,
        failure: StoreError,
        link: Link,
        attempted_head: str,
        predecessor: _Observation,
        work: Path,
    ) -> PublishResult:
        """Answer both settlement questions once, then stop.

        Stored Git state decides whether an application has to integrate
        anything. The write's own condition decides whether this invocation is
        settled at all. Neither answer constrains the other, so an open write
        outranks every Git state the pass observed: what is unresolved is this
        invocation's write, not the store's contents.
        """
        observed: Optional[_Observation] = None
        observation_failure: Optional[Exception] = None
        state = "unknown"
        merge_base = None
        parked_ref = None
        try:
            observed = self._observe(work / "settlement")
        except Exception as exc:
            # Once a head write may be open, any failed observation is evidence
            # for "unknown" rather than a reason to bypass settlement.
            observed = None
            observation_failure = exc

        if observed is not None:
            if observed.head is None:
                state = "empty"
            else:
                state = self._git_state(observed.head, attempted_head)
                if state == "diverged":
                    # Parking follows the observation, not the disposition, so
                    # an unresolved outcome preserves the same evidence an
                    # integration_required one would.
                    merge_base = self.repo.merge_base(observed.head, attempted_head)
                    parked_ref = self._park(observed)

        closed = (
            isinstance(failure, CasConflictError)
            or getattr(failure, "write_closed", False)
            or self._condition_spent(observed, predecessor)
        )
        self._log_store_contradictions(observed, predecessor)

        evidence = dict(
            attempted_head=attempted_head,
            attempted_link_uid=link.link_id,
            predecessor_head=predecessor.head,
            predecessor_etag=predecessor.etag,
            observed_head=None if observed is None else observed.head,
            observed_etag=None if observed is None else observed.etag,
            observed_link_uid=None if observed is None else observed.link_uid,
            observed_absent=state == "empty",
            write_phase=PHASE_HEAD,
            merge_base=merge_base,
            parked_ref=parked_ref,
            imported=bool(observed is not None and observed.imported),
        )

        if not closed:
            raise PublicationOutcomeUnresolvedError(
                "the head write may still take effect",
                cause=failure,
                observation_failure=observation_failure,
                **evidence,
            )
        if state == "contains":
            return PublishResult(
                disposition="already_present",
                attempted_head=attempted_head,
                observed_head=observed.head,
                observed_link_uid=observed.link_uid,
                observed_etag=observed.etag,
                predecessor_head=predecessor.head,
                predecessor_etag=predecessor.etag,
                attempted_link_uid=link.link_id,
            )
        if state == "diverged":
            raise PublicationIntegrationRequiredError(
                "the store's head diverges from local main",
                cause=failure,
                **evidence,
            )
        raise PublicationRetryableError(
            "the head write is closed and left nothing to integrate",
            cause=failure,
            observation_failure=observation_failure,
            **evidence,
        )

    @staticmethod
    def _condition_spent(
        observed: Optional[_Observation], predecessor: _Observation
    ) -> bool:
        """True when the head write's own condition can no longer hold.

        A later-chain write is conditional on the predecessor's etag, so only a
        comparable etag that differs proves it spent; an observation with no
        comparable etag proves nothing. A create-only write is conditional on
        exact absence, so any observed head spends it whatever its etag says.
        """
        if observed is None:
            return False
        if predecessor.etag is None:
            return observed.head is not None
        if observed.head is None:
            return True
        return _comparable(observed.etag) and observed.etag != predecessor.etag

    @staticmethod
    def _log_store_contradictions(
        observed: Optional[_Observation], predecessor: _Observation
    ):
        """Record observations the store's own contract says cannot happen.

        None of these change the disposition. They are logged because a store
        that produces one has broken more than this publication.
        """
        if observed is None:
            return
        if observed.head is not None and not _comparable(observed.etag):
            logger.warning(
                "settlement read head %s with no comparable etag", observed.head
            )
        if predecessor.etag is None:
            return
        if observed.head is None:
            logger.warning(
                "settlement found no head, though this invocation read head %s "
                "and chain heads are not deleted",
                predecessor.head,
            )
        elif observed.head != predecessor.head and observed.etag == predecessor.etag:
            logger.warning(
                "the head moved from %s to %s but its etag did not change",
                predecessor.head,
                observed.head,
            )

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
