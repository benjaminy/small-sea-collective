"""Micro tests for publication.

Publication is where the branch's central behavior change lives: a store may
only move forward. The old code replaced a chain containing a teammate's
commits with one that did not and reported success; these tests hold the line
that a non-descendant head stops before anything is uploaded.
"""

import pathlib

import pytest
from cod_sync_test_helpers import (
    CountingStore,
    all_refs,
    assert_no_scratch,
    commit_file,
    make_cod_sync,
    make_repo,
    make_store,
)

from cod_sync.format import decode_link, encode_link
from cod_sync.protocol import (
    MAIN_REF,
    ChainError,
    CodSync,
    NoLocalHeadError,
    PublicationIntegrationRequiredError,
)
from cod_sync.store import CasConflictError, LocalFolderStore, ObjectNotFoundError


class FrozenEmptyView:
    """A store still reporting the empty state its owner observed earlier.

    Writes go through to the real store, so a publication built on a stale
    observation reaches the same create-only head write the winner already
    performed.
    """

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def get_latest_link(self):
        raise ObjectNotFoundError("latest-link.yaml")


@pytest.fixture
def alice(scratch_dir):
    """A repo with one commit and an empty store."""
    scratch = pathlib.Path(scratch_dir)
    repo = make_repo(scratch / "alice", "alice")
    commit_file(repo, "README.md", "# Project\n")
    store = make_store(scratch / "publication")
    return repo, store


def latest(store) -> "object":
    return decode_link(store.get_latest_link()[0])


# ------------------------------------------------------------ empty store #


def test_initial_publication_writes_bundle_link_and_head(alice):
    repo, store = alice
    result = make_cod_sync(repo, store).publish()

    assert result.changed is True
    assert result.head == repo.resolve_ref(MAIN_REF)

    link = latest(store)
    assert link.previous is None
    assert link.head == result.head
    assert link.link_id == result.link_uid
    # The first link is randomly named like every other link, so two first
    # publishers cannot collide on a sentinel id.
    assert link.link_id != "initial-snapshot"
    assert store.get_link(link.link_id) == store.get_latest_link()[0]
    assert_no_scratch(repo)


def test_initial_publication_moves_no_local_ref(alice):
    repo, store = alice
    before = all_refs(repo)
    make_cod_sync(repo, store).publish()
    assert all_refs(repo) == before


def test_a_repo_with_no_main_cannot_publish(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    repo = make_repo(scratch / "empty", "alice")
    store = make_store(scratch / "publication")
    with pytest.raises(NoLocalHeadError):
        make_cod_sync(repo, store).publish()


def test_two_racing_first_publications_leave_one_winner(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    publication = scratch / "publication"
    winner = make_repo(scratch / "winner", "alice")
    commit_file(winner, "a.txt", "alice\n")
    loser = make_repo(scratch / "loser", "bob")
    commit_file(loser, "b.txt", "bob\n")

    # Both observed an empty store; the loser's write arrives second. The
    # frozen view is what makes this a race rather than a sequence.
    winner_store = make_store(publication)
    loser_store = FrozenEmptyView(LocalFolderStore(str(publication)))

    winner_result = make_cod_sync(winner, winner_store).publish()
    with pytest.raises(CasConflictError):
        make_cod_sync(loser, loser_store).publish()

    link = latest(winner_store)
    assert link.head == winner_result.head

    # The winning chain is still fetchable and the loser left only orphans.
    reader = make_repo(scratch / "reader", "carol")
    fetched = make_cod_sync(reader, LocalFolderStore(str(publication))).fetch()
    assert fetched.observed_head == winner_result.head


# --------------------------------------------------------- non-empty store #


def test_second_publication_is_incremental(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    second_head = commit_file(repo, "notes.txt", "more\n")

    result = make_cod_sync(repo, store).publish()
    assert result.changed is True
    assert result.head == second_head

    link = latest(store)
    assert link.previous is not None
    assert link.previous.link_id == first.link_uid
    assert link.previous.head == first.head
    assert len(list(pathlib.Path(store.path).glob("B-*.bundle"))) == 2


def test_publishing_an_unchanged_head_uploads_nothing(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    _bytes, etag_before = store.get_latest_link()
    objects_before = sorted(p.name for p in pathlib.Path(store.path).iterdir())

    counting = CountingStore(store)
    result = make_cod_sync(repo, counting).publish()

    assert result.changed is False
    assert result.head == first.head
    assert result.link_uid == first.link_uid
    assert store.get_latest_link()[1] == etag_before
    assert sorted(p.name for p in pathlib.Path(store.path).iterdir()) == objects_before


def test_an_unchanged_head_is_still_validated_against_its_bundle(alice):
    """Local possession of the declared commit proves nothing about the store."""
    repo, store = alice
    make_cod_sync(repo, store).publish()

    counting = CountingStore(store)
    make_cod_sync(repo, counting).publish()
    assert len(counting.bundle_reads) == 1


def test_a_stored_head_missing_locally_stops_before_upload(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    publication = scratch / "publication"
    alice = make_repo(scratch / "alice", "alice")
    commit_file(alice, "a.txt", "alice\n")
    store = make_store(publication)
    make_cod_sync(alice, store).publish()

    # Bob's history is unrelated: he does not have Alice's head at all.
    bob = make_repo(scratch / "bob", "bob")
    commit_file(bob, "b.txt", "bob\n")

    before = sorted(p.name for p in pathlib.Path(publication).iterdir())
    with pytest.raises(PublicationIntegrationRequiredError) as exc:
        make_cod_sync(bob, LocalFolderStore(str(publication))).publish()

    assert exc.value.stored_head == alice.resolve_ref(MAIN_REF)
    assert exc.value.local_head == bob.resolve_ref(MAIN_REF)
    assert exc.value.link_uid
    assert sorted(p.name for p in pathlib.Path(publication).iterdir()) == before


def test_a_non_ancestor_stored_head_stops_before_upload(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    publication = scratch / "publication"
    alice = make_repo(scratch / "alice", "alice")
    base = commit_file(alice, "a.txt", "alice\n")
    store = make_store(publication)
    make_cod_sync(alice, store).publish()

    # Bob shares Alice's base but has since diverged, and Alice publishes
    # again. Bob's next publication would drop her second commit.
    commit_file(alice, "a2.txt", "alice again\n")
    make_cod_sync(alice, store).publish()

    # Bob has all of Alice's history locally but his own main branched off the
    # base, so the stored head is present and still not an ancestor.
    bob = make_repo(scratch / "bob", "bob")
    make_cod_sync(bob, LocalFolderStore(str(publication))).fetch()
    bob.checkout_branch("main", base)
    commit_file(bob, "b.txt", "bob\n")

    _before_bytes, etag_before = store.get_latest_link()
    with pytest.raises(PublicationIntegrationRequiredError) as exc:
        make_cod_sync(bob, LocalFolderStore(str(publication))).publish()

    assert "not an ancestor" in str(exc.value)
    assert exc.value.stored_head == alice.resolve_ref(MAIN_REF)
    assert exc.value.merge_base == base
    assert store.get_latest_link()[1] == etag_before


def test_a_descendant_stored_head_publishes(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    publication = scratch / "publication"
    alice = make_repo(scratch / "alice", "alice")
    commit_file(alice, "a.txt", "alice\n")
    store = make_store(publication)
    make_cod_sync(alice, store).publish()

    bob = make_repo(scratch / "bob", "bob")
    bob_store = LocalFolderStore(str(publication))
    result = make_cod_sync(bob, bob_store).fetch()
    bob.checkout_branch("main", result.observed_head)
    bob_head = commit_file(bob, "b.txt", "bob\n")

    published = make_cod_sync(bob, bob_store).publish()
    assert published.changed is True
    assert published.head == bob_head
    assert latest(bob_store).previous.head == result.observed_head


def test_a_missing_archived_copy_of_the_head_stops_publication(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")

    (pathlib.Path(store.path) / f"L-{first.link_uid}.yaml").unlink()
    _bytes, etag_before = store.get_latest_link()
    with pytest.raises(ChainError, match="archived copy"):
        make_cod_sync(repo, store).publish()
    assert store.get_latest_link()[1] == etag_before


def test_a_mismatched_archived_copy_of_the_head_stops_publication(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")

    archived = pathlib.Path(store.path) / f"L-{first.link_uid}.yaml"
    archived.write_bytes(archived.read_bytes() + b"# nudged\n")
    _bytes, etag_before = store.get_latest_link()
    with pytest.raises(ChainError, match="differ"):
        make_cod_sync(repo, store).publish()
    assert store.get_latest_link()[1] == etag_before


def test_a_doctored_head_bundle_stops_publication(alice):
    """The store's own head must be a valid publication before it is extended."""
    repo, store = alice
    make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")

    bundle = next(pathlib.Path(store.path).glob("B-*.bundle"))
    bundle.write_bytes(b"# v2 git bundle\n" + bundle.read_bytes())
    with pytest.raises(Exception):
        make_cod_sync(repo, store).publish()


def test_an_unavailable_extra_prerequisite_stops_publication(alice):
    """A publisher must not extend a current bundle it cannot validate."""
    repo, store = alice
    make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")
    make_cod_sync(repo, store).publish()

    link = latest(store)
    bundle = pathlib.Path(store.path) / f"B-{link.bundle_id}.bundle"
    lines = bundle.read_bytes().splitlines(keepends=True)
    for index, line in enumerate(lines[1:], start=1):
        if not line.startswith((b"-", b"@")):
            lines.insert(index, b"-" + b"d" * 40 + b" hidden\n")
            break
    bundle.write_bytes(b"".join(lines))

    commit_file(repo, "later.txt", "later\n")
    objects_before = sorted(path.name for path in pathlib.Path(store.path).iterdir())
    _bytes, etag_before = store.get_latest_link()

    with pytest.raises(ChainError, match="unavailable prerequisites"):
        make_cod_sync(repo, store).publish()

    assert sorted(path.name for path in pathlib.Path(store.path).iterdir()) == objects_before
    assert store.get_latest_link()[1] == etag_before


def test_a_cas_conflict_on_a_non_empty_chain_is_reported(alice):
    repo, store = alice
    make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")

    class StaleEtagStore:
        """Reports a head that has already moved on by write time."""

        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def put_latest_link(self, data, expected_etag, link_uid=None):
            return self.inner.put_latest_link(data, "stale-etag", link_uid=link_uid)

    with pytest.raises(CasConflictError):
        make_cod_sync(repo, StaleEtagStore(store)).publish()


def test_publication_cleans_its_temporary_directory(alice, monkeypatch):
    repo, store = alice
    seen = []

    real = CodSync._require_bundle_matches

    def record(self, link, bundle_path):
        seen.append(pathlib.Path(bundle_path).parent)
        return real(self, link, bundle_path)

    monkeypatch.setattr(CodSync, "_require_bundle_matches", record)
    make_cod_sync(repo, store).publish()

    assert seen, "publication never inspected a bundle"
    for work_dir in seen:
        assert not work_dir.exists()
    assert_no_scratch(repo)


def test_a_failed_publication_still_cleans_up(scratch_dir, monkeypatch):
    scratch = pathlib.Path(scratch_dir)
    repo = make_repo(scratch / "alice", "alice")
    commit_file(repo, "a.txt", "alice\n")
    store = make_store(scratch / "publication")
    seen = []

    real = CodSync._require_bundle_matches

    def explode(self, link, bundle_path):
        seen.append(pathlib.Path(bundle_path).parent)
        real(self, link, bundle_path)
        raise RuntimeError("upload interrupted")

    monkeypatch.setattr(CodSync, "_require_bundle_matches", explode)
    with pytest.raises(RuntimeError):
        make_cod_sync(repo, store).publish()

    assert seen
    for work_dir in seen:
        assert not work_dir.exists()
    assert_no_scratch(repo)


def test_a_lost_head_response_is_not_retried_blindly(alice):
    """The write may have landed, so the answer is to reread, not to retry.

    A retry against the etag the publisher still believes in would either fail
    or, worse, overwrite whatever actually won the race.
    """
    from cod_sync.store import PublicationOutcomeUnknownError, StoreTransportError

    repo, store = alice
    make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")

    class LosesTheResponse:
        """Performs the head write, then loses the answer on the way back."""

        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def put_latest_link(self, data, expected_etag, link_uid=None):
            self.inner.put_latest_link(data, expected_etag, link_uid=link_uid)
            raise PublicationOutcomeUnknownError(
                "connection reset after the head write",
                expected_etag=expected_etag,
                link_uid=link_uid,
            ) from StoreTransportError("connection reset")

    with pytest.raises(PublicationOutcomeUnknownError) as exc:
        make_cod_sync(repo, LosesTheResponse(store)).publish()

    # Rereading shows the write did in fact take effect.
    assert latest(store).link_id == exc.value.link_uid
    assert latest(store).head == repo.resolve_ref(MAIN_REF)
