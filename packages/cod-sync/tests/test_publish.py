"""Micro tests for publication.

Two properties carry the weight here. A store may only move forward: a
non-descendant head stops before anything is uploaded, and the competing head
is preserved for whoever integrates it. And one invocation gets one fixed
envelope — at most one head write and at most two validated observation passes
— after which every terminal result follows from what those passes saw about
stored Git state and about whether this invocation's write can still land.
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

from cod_sync.format import LinkFormatError, decode_link, encode_link
from cod_sync.protocol import (
    MAIN_REF,
    ChainError,
    CodSync,
    NoLocalHeadError,
    PublicationIntegrationRequiredError,
    PublicationOutcomeUnresolvedError,
    PublicationRetryableError,
    parked_ref_name,
)
from cod_sync.store import (
    CasConflictError,
    LocalFolderStore,
    ObjectNotFoundError,
    StoreProviderError,
)


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

    assert result.disposition == "published"
    assert result.observed_head == repo.resolve_ref(MAIN_REF)

    link = latest(store)
    assert link.previous is None
    assert link.head == result.observed_head
    assert link.link_id == result.observed_link_uid
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
    # The frozen view still reports absence when settlement rereads, so the
    # loser proves only that its own create-only write is over.
    with pytest.raises(PublicationRetryableError) as exc:
        make_cod_sync(loser, loser_store).publish()
    assert isinstance(exc.value.cause, CasConflictError)
    assert exc.value.observed_absent is True

    link = latest(winner_store)
    assert link.head == winner_result.observed_head

    # The winning chain is still fetchable and the loser left only orphans.
    reader = make_repo(scratch / "reader", "carol")
    fetched = make_cod_sync(reader, LocalFolderStore(str(publication))).fetch()
    assert fetched.observed_head == winner_result.observed_head


# --------------------------------------------------------- non-empty store #


def test_second_publication_is_incremental(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    second_head = commit_file(repo, "notes.txt", "more\n")

    result = make_cod_sync(repo, store).publish()
    assert result.disposition == "published"
    assert result.observed_head == second_head

    link = latest(store)
    assert link.previous is not None
    assert link.previous.link_id == first.observed_link_uid
    assert link.previous.head == first.observed_head
    assert len(list(pathlib.Path(store.path).glob("B-*.bundle"))) == 2


def test_publishing_an_unchanged_head_uploads_nothing(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    _bytes, etag_before = store.get_latest_link()
    objects_before = sorted(p.name for p in pathlib.Path(store.path).iterdir())

    counting = CountingStore(store)
    result = make_cod_sync(repo, counting).publish()

    assert result.disposition == "already_present"
    assert result.observed_head == first.observed_head
    assert result.observed_link_uid == first.observed_link_uid
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

    assert exc.value.observed_head == alice.resolve_ref(MAIN_REF)
    assert exc.value.attempted_head == bob.resolve_ref(MAIN_REF)
    assert exc.value.observed_link_uid
    # Bob did not have Alice's head at all, so the observation pass imported it
    # in order to compare, park, and report it.
    assert exc.value.imported is True
    assert bob.resolve_ref(exc.value.parked_ref) == exc.value.observed_head
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

    assert exc.value.observed_head == alice.resolve_ref(MAIN_REF)
    assert exc.value.merge_base == base
    assert bob.resolve_ref(exc.value.parked_ref) == exc.value.observed_head
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
    assert published.disposition == "published"
    assert published.observed_head == bob_head
    assert latest(bob_store).previous.head == result.observed_head


def test_a_missing_archived_copy_of_the_head_stops_publication(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")

    (pathlib.Path(store.path) / f"L-{first.observed_link_uid}.yaml").unlink()
    _bytes, etag_before = store.get_latest_link()
    with pytest.raises(ChainError, match="archived copy"):
        make_cod_sync(repo, store).publish()
    assert store.get_latest_link()[1] == etag_before


def test_a_mismatched_archived_copy_of_the_head_stops_publication(alice):
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")

    archived = pathlib.Path(store.path) / f"L-{first.observed_link_uid}.yaml"
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


# ------------------------------------------------------ the fixed envelope #


class ScriptedStore:
    """Counts head writes and observation passes, and can fail either.

    "At most one head write and at most two observation passes" is the central
    claim of the publication boundary, so it has to be measured rather than
    asserted. This is the instrument for that, plus the smallest fault
    injection the settlement matrix needs. It is deliberately not a general
    fault-scripting framework.
    """

    def __init__(self, inner, *, head_write=None, reads=()):
        self.inner = inner
        self.head_writes = 0
        self.latest_reads = 0
        self._head_write = head_write
        self._reads = list(reads)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def get_latest_link(self):
        index = self.latest_reads
        self.latest_reads += 1
        if index < len(self._reads) and self._reads[index] is not None:
            return self._reads[index](self.inner)
        return self.inner.get_latest_link()

    def put_latest_link(self, data, expected_etag, link_uid=None):
        self.head_writes += 1
        if self._head_write is not None:
            return self._head_write(self.inner, data, expected_etag, link_uid)
        return self.inner.put_latest_link(data, expected_etag, link_uid=link_uid)


def assert_within_envelope(store):
    assert store.head_writes <= 1
    assert store.latest_reads <= 2


def inconclusive():
    """A failure that proves nothing about the write's future."""
    return StoreProviderError("the Hub answered 500")


def conclusive():
    """A failure the store proves cannot take effect later."""
    failure = StoreProviderError("the write is over")
    failure.write_closed = True
    return failure


def refuse(make_failure):
    def write(inner, data, expected_etag, link_uid):
        raise make_failure()

    return write


def apply_then_fail(make_failure):
    def write(inner, data, expected_etag, link_uid):
        inner.put_latest_link(data, expected_etag, link_uid=link_uid)
        raise make_failure()

    return write


def absent(inner):
    raise ObjectNotFoundError("latest-link.yaml")


def unreadable(inner):
    raise StoreProviderError("the Hub answered 500")


def with_etag(value):
    def read(inner):
        data, _etag = inner.get_latest_link()
        return data, value

    return read


def frozen(snapshot):
    """Replay a head observation that has since been overtaken."""

    def read(inner):
        return snapshot

    return read


def malformed(inner):
    return b"not a cod sync link", "settlement-etag"


def ready_to_extend(alice):
    """Publish once, then commit again: a later-chain write is next."""
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    commit_file(repo, "notes.txt", "more\n")
    return repo, store, first


def test_the_attempted_head_is_frozen_for_the_whole_invocation(alice):
    """One invocation publishes one state, whatever main does meanwhile."""
    repo, store = alice
    attempted = repo.resolve_ref(MAIN_REF)

    class CommitsDuringInitialObservation:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def get_latest_link(self):
            try:
                return self.inner.get_latest_link()
            except ObjectNotFoundError:
                commit_file(repo, "racing.txt", "committed mid-publication\n")
                raise

    result = make_cod_sync(repo, CommitsDuringInitialObservation(store)).publish()

    assert result.attempted_head == attempted
    assert result.observed_head == attempted
    assert latest(store).head == attempted
    assert repo.resolve_ref(MAIN_REF) != attempted


def test_an_initial_observation_failure_is_retryable(alice):
    repo, store = alice
    scripted = ScriptedStore(store, reads=[unreadable])

    with pytest.raises(PublicationRetryableError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert exc.value.write_phase is None
    assert exc.value.cause is None
    assert isinstance(exc.value.observation_failure, StoreProviderError)
    assert scripted.head_writes == 0
    assert_within_envelope(scripted)


def test_a_covering_stored_head_that_is_not_local_is_already_present(scratch_dir):
    """Coverage is a Git fact, so it survives the head not being local yet."""
    scratch = pathlib.Path(scratch_dir)
    publication = scratch / "publication"
    alice = make_repo(scratch / "alice", "alice")
    commit_file(alice, "a.txt", "alice\n")
    store = make_store(publication)
    first = make_cod_sync(alice, store).publish()

    bob = make_repo(scratch / "bob", "bob")
    bob_store = ScriptedStore(LocalFolderStore(str(publication)))
    make_cod_sync(bob, bob_store).fetch()
    bob.checkout_branch("main", first.observed_head)

    # Alice publishes a descendant Bob has never seen.
    covering = commit_file(alice, "a2.txt", "alice again\n")
    make_cod_sync(alice, store).publish()

    bob_store = ScriptedStore(LocalFolderStore(str(publication)))
    refs_before = all_refs(bob)
    result = make_cod_sync(bob, bob_store).publish()

    assert result.disposition == "already_present"
    assert result.observed_head == covering
    assert result.attempted_head == first.observed_head
    assert result.predecessor_head is None
    # Importing is all an observation pass may do to the repository.
    assert bob.has_commit(covering)
    assert all_refs(bob) == refs_before
    assert bob_store.head_writes == 0
    assert_within_envelope(bob_store)


# ------------------------------------------------- settlement: later chain #


def test_an_unchanged_head_after_an_inconclusive_failure_is_unresolved(alice):
    repo, store, first = ready_to_extend(alice)
    scripted = ScriptedStore(store, head_write=refuse(inconclusive))

    with pytest.raises(PublicationOutcomeUnresolvedError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert exc.value.write_phase == "head"
    assert exc.value.observed_head == first.observed_head
    assert exc.value.observed_etag == exc.value.predecessor_etag
    assert exc.value.attempted_link_uid
    assert_within_envelope(scripted)


def test_a_changed_etag_on_an_unchanged_head_closes_the_write(alice):
    """The condition is spent, which settles this invocation on its own."""
    repo, store, first = ready_to_extend(alice)
    scripted = ScriptedStore(
        store, head_write=refuse(inconclusive), reads=[None, with_etag("moved-on")]
    )

    with pytest.raises(PublicationRetryableError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert exc.value.observed_head == first.observed_head
    assert exc.value.observed_etag == "moved-on"
    assert_within_envelope(scripted)


def test_a_strict_ancestor_after_a_conclusive_failure_is_retryable(alice):
    repo, store, first = ready_to_extend(alice)
    scripted = ScriptedStore(store, head_write=refuse(conclusive))

    with pytest.raises(PublicationRetryableError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert exc.value.observed_head == first.observed_head
    assert exc.value.observation_failure is None
    assert store.get_latest_link()[0] == store.get_link(first.observed_link_uid)
    assert_within_envelope(scripted)


def test_confirmed_absence_after_a_later_chain_write_closes_it(alice):
    """A head this invocation read cannot come back, so the etag is spent."""
    repo, store, _first = ready_to_extend(alice)
    scripted = ScriptedStore(
        store, head_write=refuse(inconclusive), reads=[None, absent]
    )

    with pytest.raises(PublicationRetryableError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert exc.value.observed_absent is True
    assert exc.value.observed_head is None
    assert_within_envelope(scripted)


def test_an_unreadable_pass_leaves_a_conclusive_failure_retryable(alice):
    repo, store, _first = ready_to_extend(alice)
    scripted = ScriptedStore(
        store, head_write=refuse(conclusive), reads=[None, unreadable]
    )

    with pytest.raises(PublicationRetryableError) as exc:
        make_cod_sync(repo, scripted).publish()

    # Nothing was observed, so this says no divergence was seen, not none exists.
    assert exc.value.observed_head is None
    assert isinstance(exc.value.observation_failure, StoreProviderError)
    assert_within_envelope(scripted)


def test_an_unreadable_pass_leaves_an_inconclusive_failure_unresolved(alice):
    repo, store, _first = ready_to_extend(alice)
    scripted = ScriptedStore(
        store, head_write=refuse(inconclusive), reads=[None, unreadable]
    )

    with pytest.raises(PublicationOutcomeUnresolvedError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert isinstance(exc.value.observation_failure, StoreProviderError)
    assert_within_envelope(scripted)


def test_a_malformed_settlement_observation_preserves_the_open_write(alice):
    repo, store, _first = ready_to_extend(alice)
    scripted = ScriptedStore(
        store, head_write=refuse(inconclusive), reads=[None, malformed]
    )

    with pytest.raises(PublicationOutcomeUnresolvedError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert isinstance(exc.value.observation_failure, LinkFormatError)
    assert_within_envelope(scripted)


@pytest.mark.parametrize("etag", [None, ""])
def test_an_incomparable_settlement_etag_leaves_the_write_open(alice, etag):
    """Neither None nor "" can prove a conditional write is spent."""
    repo, store, _first = ready_to_extend(alice)
    scripted = ScriptedStore(
        store, head_write=apply_then_fail(inconclusive), reads=[None, with_etag(etag)]
    )

    with pytest.raises(PublicationOutcomeUnresolvedError) as exc:
        make_cod_sync(repo, scripted).publish()

    # The pass even saw the attempted state stored; an open write outranks it.
    assert exc.value.observed_head == repo.resolve_ref(MAIN_REF)
    assert exc.value.observed_etag == etag
    assert_within_envelope(scripted)


def test_an_applied_but_unacknowledged_head_write_is_already_present(alice):
    """The lost response is not retried: rereading settles it."""
    repo, store, _first = ready_to_extend(alice)
    scripted = ScriptedStore(store, head_write=apply_then_fail(inconclusive))

    result = make_cod_sync(repo, scripted).publish()

    assert result.disposition == "already_present"
    assert result.observed_head == repo.resolve_ref(MAIN_REF)
    assert result.observed_link_uid == result.attempted_link_uid
    assert_within_envelope(scripted)


# ------------------------------------------------ settlement: divergence #


def diverging_race(scratch):
    """Bob publishes against a head Alice has already replaced with her own.

    Returns Bob's repo, the publication path, Alice's competing head, the merge
    base, and the stale observation Bob's first pass replays.
    """
    publication = scratch / "publication"
    alice = make_repo(scratch / "alice", "alice")
    base = commit_file(alice, "a.txt", "alice\n")
    store = make_store(publication)
    make_cod_sync(alice, store).publish()

    bob = make_repo(scratch / "bob", "bob")
    make_cod_sync(bob, LocalFolderStore(str(publication))).fetch()
    bob.checkout_branch("main", base)
    commit_file(bob, "b.txt", "bob\n")

    stale = store.get_latest_link()
    competing = commit_file(alice, "a2.txt", "alice again\n")
    make_cod_sync(alice, store).publish()
    return bob, publication, competing, base, stale


def test_divergence_under_a_closed_write_is_integration_required(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    bob, publication, competing, base, stale = diverging_race(scratch)

    # Bob's own etag lost the race, so his write is conclusively over.
    scripted = ScriptedStore(LocalFolderStore(str(publication)), reads=[frozen(stale)])
    with pytest.raises(PublicationIntegrationRequiredError) as exc:
        make_cod_sync(bob, scripted).publish()

    assert isinstance(exc.value.cause, CasConflictError)
    assert exc.value.observed_head == competing
    assert exc.value.predecessor_head == stale_head(stale)
    assert exc.value.merge_base == base
    assert bob.resolve_ref(exc.value.parked_ref) == competing
    assert exc.value.parked_ref == parked_ref_name(exc.value.observed_link_uid)
    assert_within_envelope(scripted)


def test_divergence_under_an_open_write_keeps_its_evidence(scratch_dir):
    """Choosing the honest disposition never costs the caller evidence."""
    scratch = pathlib.Path(scratch_dir)
    bob, publication, competing, base, stale = diverging_race(scratch)

    scripted = ScriptedStore(
        LocalFolderStore(str(publication)),
        head_write=refuse(inconclusive),
        reads=[frozen(stale), with_etag(None)],
    )
    with pytest.raises(PublicationOutcomeUnresolvedError) as exc:
        make_cod_sync(bob, scripted).publish()

    assert exc.value.observed_head == competing
    assert exc.value.merge_base == base
    assert bob.resolve_ref(exc.value.parked_ref) == competing
    assert_within_envelope(scripted)


def stale_head(snapshot):
    return decode_link(snapshot[0]).head


# ------------------------------------------------ settlement: create-only #


def test_confirmed_absence_after_a_create_only_write_leaves_it_open(alice):
    """A create-only write's condition is absence, which absence does not spend."""
    repo, store = alice
    scripted = ScriptedStore(store, head_write=refuse(inconclusive))

    with pytest.raises(PublicationOutcomeUnresolvedError) as exc:
        make_cod_sync(repo, scripted).publish()

    assert exc.value.predecessor_head is None
    assert exc.value.observed_absent is True
    assert_within_envelope(scripted)


def test_any_head_spends_a_create_only_write(scratch_dir):
    """Presence closes the write whatever the observed head's etag says."""
    scratch = pathlib.Path(scratch_dir)
    publication = scratch / "publication"
    winner = make_repo(scratch / "winner", "alice")
    commit_file(winner, "a.txt", "alice\n")
    make_cod_sync(winner, make_store(publication)).publish()

    loser = make_repo(scratch / "loser", "bob")
    commit_file(loser, "b.txt", "bob\n")
    scripted = ScriptedStore(
        LocalFolderStore(str(publication)),
        head_write=refuse(inconclusive),
        reads=[absent, with_etag(None)],
    )

    with pytest.raises(PublicationIntegrationRequiredError) as exc:
        make_cod_sync(loser, scripted).publish()

    assert exc.value.observed_head == winner.resolve_ref(MAIN_REF)
    assert loser.resolve_ref(exc.value.parked_ref) == exc.value.observed_head
    assert_within_envelope(scripted)


# ------------------------------------------------------- the etag contract #


@pytest.mark.parametrize("etag", [None, ""])
def test_a_chain_head_without_a_comparable_etag_is_refused(alice, etag):
    """A store that cannot express a conditional head write says so up front."""
    repo, store, _first = ready_to_extend(alice)
    scripted = ScriptedStore(store, reads=[with_etag(etag)])
    objects_before = sorted(p.name for p in pathlib.Path(store.path).iterdir())

    with pytest.raises(ChainError, match="comparable etag"):
        make_cod_sync(repo, scripted).publish()

    assert scripted.head_writes == 0
    assert sorted(p.name for p in pathlib.Path(store.path).iterdir()) == objects_before


def test_a_covering_head_needs_no_etag(alice):
    """A head that already covers the attempt needs no successor written."""
    repo, store = alice
    first = make_cod_sync(repo, store).publish()
    scripted = ScriptedStore(store, reads=[with_etag(None)])

    result = make_cod_sync(repo, scripted).publish()

    assert result.disposition == "already_present"
    assert result.observed_head == first.observed_head
    assert scripted.head_writes == 0
