"""Micro tests for fetch, chain traversal, and pinning.

The old fetch returned a SHA the link claimed, without ever asking the bundle
whether it carried that commit, and it created remote-tracking refs as a side
effect of using `git fetch`. These tests pin down the replacement: nothing is
believed without the bundle header agreeing, and nothing durable moves except
a pin the caller asked for.

Several fixtures here did not exist in any form before this branch — a chain
longer than two links, a walk backward across more than one missing link, and
a bundle whose actual prerequisites disagree with its link.
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
    working_tree_files,
)

from cod_sync.format import decode_link, encode_link
from cod_sync.protocol import (
    MAIN_REF,
    ChainError,
    NoPublishedHeadError,
    PinIntegrationRequiredError,
)
from cod_sync.store import LocalFolderStore

PIN = "refs/peers/alice/main"


def build_chain(scratch, length):
    """Publish `length` successive heads and return (repo, store_path, heads)."""
    repo = make_repo(scratch / "alice", "alice")
    publication = scratch / "publication"
    store = make_store(publication)
    heads = []
    for index in range(length):
        commit_file(repo, f"file{index}.txt", f"content {index}\n")
        heads.append(make_cod_sync(repo, store).publish().head)
    return repo, publication, heads


def reader(scratch, name="bob"):
    return make_repo(scratch / name, name)


def store_at(publication):
    return LocalFolderStore(str(publication))


# ------------------------------------------------------------- empty store #


def test_an_empty_store_has_no_head(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    bob = reader(scratch)
    with pytest.raises(NoPublishedHeadError):
        make_cod_sync(bob, make_store(scratch / "publication")).fetch()


# ------------------------------------------------------------ ordinary use #


def test_initial_fetch_imports_the_full_snapshot(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)

    result = make_cod_sync(bob, store_at(publication)).fetch()
    assert result.observed_head == heads[-1]
    assert bob.has_commit(heads[-1])
    bob.checkout_branch("main", result.observed_head)
    assert working_tree_files(bob) == working_tree_files(alice)


def test_incremental_fetch_adds_only_the_new_head(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)
    make_cod_sync(bob, store_at(publication)).fetch()

    commit_file(alice, "later.txt", "later\n")
    second = make_cod_sync(alice, store_at(publication)).publish().head

    counting = CountingStore(store_at(publication))
    result = make_cod_sync(bob, counting).fetch()
    assert result.observed_head == second
    # The prerequisite was already local, so no predecessor was read.
    assert counting.link_reads == []
    assert len(counting.bundle_reads) == 1


def test_a_cold_start_walks_a_chain_of_three(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice, publication, heads = build_chain(scratch, 3)
    bob = reader(scratch)

    counting = CountingStore(store_at(publication))
    result = make_cod_sync(bob, counting).fetch()

    assert result.observed_head == heads[-1]
    for head in heads:
        assert bob.has_commit(head)
    bob.checkout_branch("main", result.observed_head)
    assert working_tree_files(bob) == working_tree_files(alice)


def test_each_bundle_is_downloaded_exactly_once(scratch_dir):
    """The old clone walked the chain per link, so an n-link chain cost n^2."""
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 4)
    bob = reader(scratch)

    counting = CountingStore(store_at(publication))
    make_cod_sync(bob, counting).fetch()

    assert len(counting.bundle_reads) == len(heads)
    assert len(set(counting.bundle_reads)) == len(counting.bundle_reads)


def test_a_walk_crosses_more_than_one_missing_link(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)
    make_cod_sync(bob, store_at(publication)).fetch()

    # Three more publications land while Bob is away.
    for index in (1, 2, 3):
        commit_file(alice, f"away{index}.txt", f"away {index}\n")
        heads.append(make_cod_sync(alice, store_at(publication)).publish().head)

    counting = CountingStore(store_at(publication))
    result = make_cod_sync(bob, counting).fetch()

    assert result.observed_head == heads[-1]
    # Newest first, stopping at the head Bob already had: three bundles to
    # import, reached by reading two archived predecessors.
    assert len(counting.link_reads) == 2
    assert len(counting.bundle_reads) == 3
    for head in heads:
        assert bob.has_commit(head)


def test_an_already_current_fetch_still_reads_the_latest_bundle(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)
    make_cod_sync(bob, store_at(publication)).fetch()

    counting = CountingStore(store_at(publication))
    result = make_cod_sync(bob, counting).fetch()

    assert result.observed_head == heads[-1]
    assert len(counting.bundle_reads) == 1  # validated, but nothing to walk
    assert counting.link_reads == []


def test_an_already_current_fetch_still_catches_a_doctored_bundle(scratch_dir):
    """Possessing the declared commit is not proof the store published it."""
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)
    make_cod_sync(bob, store_at(publication)).fetch()

    bundle = next(pathlib.Path(publication).glob("B-*.bundle"))
    bundle.write_bytes(bundle.read_bytes().replace(b"refs/heads/main", b"refs/heads/mane"))

    before = all_refs(bob)
    with pytest.raises(ChainError):
        make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)
    assert all_refs(bob) == before


# ----------------------------------------------------------------- no refs #


def test_fetch_creates_no_implicit_ref(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)

    before = all_refs(bob)
    make_cod_sync(bob, store_at(publication)).fetch()

    assert all_refs(bob) == before
    assert not (bob.git_dir / "FETCH_HEAD").exists()
    assert bob._run(["remote"]).stdout.strip() == ""
    assert_no_scratch(bob)


def test_fetch_with_a_pin_creates_exactly_that_ref(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)

    before = all_refs(bob)
    result = make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)

    assert all_refs(bob) == {**before, PIN: heads[-1]}
    assert result.pinned_head == heads[-1]
    assert result.pin_disposition == "created"


# -------------------------------------------------------------------- pins #


def test_a_pin_advances_forward(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)
    make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)

    commit_file(alice, "next.txt", "next\n")
    second = make_cod_sync(alice, store_at(publication)).publish().head
    result = make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)

    assert result.pin_disposition == "advanced"
    assert result.pinned_head == second


def test_an_equal_pin_is_unchanged(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)
    make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)
    result = make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)

    assert result.pin_disposition == "unchanged"
    assert result.pinned_head == heads[-1]


def test_a_stale_observation_leaves_a_newer_pin_alone(scratch_dir):
    """An out-of-order fetch must not walk the pin backward."""
    scratch = pathlib.Path(scratch_dir)
    alice = make_repo(scratch / "alice", "alice")
    old_publication = scratch / "old"
    new_publication = scratch / "new"
    old_store = make_store(old_publication)
    new_store = make_store(new_publication)

    first = commit_file(alice, "a.txt", "one\n")
    make_cod_sync(alice, old_store).publish()
    second = commit_file(alice, "b.txt", "two\n")
    make_cod_sync(alice, new_store).publish()

    bob = reader(scratch)
    make_cod_sync(bob, store_at(new_publication)).fetch(pin_to_ref=PIN)
    result = make_cod_sync(bob, store_at(old_publication)).fetch(pin_to_ref=PIN)

    assert result.observed_head == first
    assert result.pin_disposition == "stale"
    assert result.pinned_head == second
    assert bob.resolve_ref(PIN) == second


def test_a_divergent_pin_pauses_for_integration(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)
    unrelated = commit_file(bob, "own.txt", "bob's own\n")
    bob._run(["update-ref", PIN, unrelated])

    with pytest.raises(PinIntegrationRequiredError) as exc:
        make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)

    assert exc.value.observed_head == heads[-1]
    assert bob.resolve_ref(PIN) == unrelated


# --------------------------------------------------------- malformed chains #


def rewrite_latest(publication, mutate):
    """Apply mutate to the store's head link and write it back everywhere."""
    publication = pathlib.Path(publication)
    latest_path = publication / "latest-link.yaml"
    link = decode_link(latest_path.read_bytes())
    mutated = mutate(link)
    data = encode_link(mutated)
    latest_path.write_bytes(data)
    (publication / f"L-{mutated.link_id}.yaml").write_bytes(data)
    return mutated


def test_a_head_mismatch_is_rejected_before_any_ref_moves(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)

    from dataclasses import replace

    rewrite_latest(publication, lambda link: replace(link, head="c" * 40))

    before = all_refs(bob)
    with pytest.raises(ChainError) as exc:
        make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)
    assert exc.value.advertised_head == heads[-1]
    assert exc.value.declared_head == "c" * 40
    assert all_refs(bob) == before


def test_an_initial_bundle_claiming_a_prerequisite_is_rejected(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 1)
    bob = reader(scratch)

    from dataclasses import replace

    from cod_sync.format import Predecessor

    rewrite_latest(
        publication,
        lambda link: replace(
            link, previous=Predecessor(link_id="f" * 16, head="d" * 40)
        ),
    )
    with pytest.raises(ChainError) as exc:
        make_cod_sync(bob, store_at(publication)).fetch()
    assert exc.value.declared_prerequisites == {"d" * 40}
    assert exc.value.actual_prerequisites == set()


def test_a_hidden_extra_prerequisite_is_rejected(scratch_dir):
    """A bundle carrying more prerequisites than its link admits to."""
    scratch = pathlib.Path(scratch_dir)
    alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)

    # Publish a merge, then rebuild its bundle excluding the side parent too.
    # The pack now needs a second prerequisite that the link never declares —
    # exactly the shape that would let a bundle quietly omit history.
    alice.checkout_branch("side", heads[0])
    side = commit_file(alice, "side.txt", "side\n")
    alice.checkout_branch("main", heads[-1])
    alice.merge("side")
    make_cod_sync(alice, store_at(publication)).publish()

    latest_link = decode_link((pathlib.Path(publication) / "latest-link.yaml").read_bytes())
    target = pathlib.Path(publication) / f"B-{latest_link.bundle_id}.bundle"
    target.unlink()
    alice.create_bundle(target, [f"^{heads[-1]}", f"^{side}", MAIN_REF])

    before = all_refs(bob)
    with pytest.raises(ChainError) as exc:
        make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)
    assert exc.value.actual_prerequisites > exc.value.declared_prerequisites
    assert all_refs(bob) == before


def test_a_missing_predecessor_is_rejected(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)

    latest_link = decode_link((pathlib.Path(publication) / "latest-link.yaml").read_bytes())
    (pathlib.Path(publication) / f"L-{latest_link.previous.link_id}.yaml").unlink()

    before = all_refs(bob)
    with pytest.raises(ChainError, match="predecessor the store does not hold"):
        make_cod_sync(bob, store_at(publication)).fetch(pin_to_ref=PIN)
    assert all_refs(bob) == before


def test_a_cycle_terminates(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)

    publication = pathlib.Path(publication)
    latest_link = decode_link((publication / "latest-link.yaml").read_bytes())
    # Point the predecessor's archived copy back at the newest link.
    (publication / f"L-{latest_link.previous.link_id}.yaml").write_bytes(
        (publication / "latest-link.yaml").read_bytes()
    )

    with pytest.raises(ChainError):
        make_cod_sync(bob, store_at(publication)).fetch()


def test_an_archived_link_read_under_the_wrong_id_is_rejected(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)

    publication = pathlib.Path(publication)
    latest_link = decode_link((publication / "latest-link.yaml").read_bytes())
    archived_path = publication / f"L-{latest_link.previous.link_id}.yaml"
    archived = decode_link(archived_path.read_bytes())

    from dataclasses import replace

    archived_path.write_bytes(encode_link(replace(archived, link_id="0" * 16)))

    with pytest.raises(ChainError, match="own id"):
        make_cod_sync(bob, store_at(publication)).fetch()


def test_an_inconsistent_predecessor_head_is_rejected(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 3)
    bob = reader(scratch)

    publication = pathlib.Path(publication)
    latest_link = decode_link((publication / "latest-link.yaml").read_bytes())
    archived_path = publication / f"L-{latest_link.previous.link_id}.yaml"
    archived = decode_link(archived_path.read_bytes())

    from dataclasses import replace

    archived_path.write_bytes(encode_link(replace(archived, head="e" * 40)))

    with pytest.raises(ChainError, match="different head"):
        make_cod_sync(bob, store_at(publication)).fetch()


def test_a_version_regression_is_rejected(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)

    publication = pathlib.Path(publication)
    latest_path = publication / "latest-link.yaml"
    latest_link = decode_link(latest_path.read_bytes())

    from dataclasses import replace

    # The newest link is older than the one it extends.
    archived_path = publication / f"L-{latest_link.previous.link_id}.yaml"
    archived = decode_link(archived_path.read_bytes())
    archived_path.write_bytes(encode_link(replace(archived, version="2.9.0")))

    with pytest.raises(ChainError, match="regresses"):
        make_cod_sync(bob, store_at(publication)).fetch()


def test_fetch_cleans_its_temporary_directory(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    _alice, publication, heads = build_chain(scratch, 3)
    bob = reader(scratch)

    seen = []
    inner = store_at(publication)

    class RecordingStore(CountingStore):
        def download_bundle(self, bundle_uid, local_path):
            seen.append(pathlib.Path(local_path).parent)
            return super().download_bundle(bundle_uid, local_path)

    make_cod_sync(bob, RecordingStore(inner)).fetch()

    assert seen
    assert len(set(seen)) == 1, "one operation must use one temporary directory"
    assert not seen[0].exists()
    assert_no_scratch(bob)


def test_a_published_merge_round_trips(scratch_dir):
    """Merging a branch off an older base adds prerequisites git records itself.

    They sit behind the declared predecessor, so they are covered rather than
    hidden — and this is the ordinary shape of adopting a teammate's work.
    """
    scratch = pathlib.Path(scratch_dir)
    alice, publication, heads = build_chain(scratch, 2)
    bob = reader(scratch)
    make_cod_sync(bob, store_at(publication)).fetch()

    alice.checkout_branch("side", heads[0])
    commit_file(alice, "side.txt", "side\n")
    alice.checkout_branch("main", heads[-1])
    alice.merge("side")
    merged = make_cod_sync(alice, store_at(publication)).publish()

    result = make_cod_sync(bob, store_at(publication)).fetch()
    assert result.observed_head == merged.head
    bob.checkout_branch("main", result.observed_head)
    assert working_tree_files(bob) == working_tree_files(alice)
