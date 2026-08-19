"""End-to-end round trips through the bundle protocol.

Two people, two stores, and no synthetic remotes anywhere: what a caller
actually does is init, fetch, adopt the observed head, commit, publish.
"""

import pathlib

from cod_sync_test_helpers import (
    all_refs,
    assert_no_scratch,
    commit_file,
    make_cod_sync,
    make_repo,
    make_store,
    working_tree_files,
)

from cod_sync.store import LocalFolderStore


def test_cold_start_then_incremental_round_trip(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice_pub = scratch / "alice-publication"
    bob_pub = scratch / "bob-publication"

    # ---- Alice publishes two heads ----
    alice = make_repo(scratch / "alice", "alice")
    commit_file(alice, "README.md", "# My Project\n")
    commit_file(alice, "notes.txt", "remember to buy milk\n")
    alice_store = make_store(alice_pub)
    make_cod_sync(alice, alice_store).publish()

    commit_file(alice, "plan.txt", "step 1: profit\n")
    second = make_cod_sync(alice, alice_store).publish()
    assert second.changed is True

    # ---- Bob cold-starts: init, fetch, check out the observed head ----
    bob = make_repo(scratch / "bob", "bob")
    result = make_cod_sync(bob, LocalFolderStore(str(alice_pub))).fetch()
    bob.checkout_branch("main", result.observed_head)
    assert working_tree_files(bob) == working_tree_files(alice)

    # ---- Bob modifies, adds, and deletes, then publishes to his own store ----
    (bob.work_tree / "README.md").write_text("# My Project\n\nUpdated by Bob.\n")
    (bob.work_tree / "notes.txt").unlink()
    (bob.work_tree / "todo.txt").write_text("- write tests\n- ship it\n")
    bob.stage(None)
    bob.commit("Bob's changes")

    bob_store = make_store(bob_pub)
    make_cod_sync(bob, bob_store).publish()
    assert len(list(bob_pub.glob("B-*.bundle"))) == 1

    # ---- Alice parks Bob's head on a peer ref, then merges it explicitly ----
    parked = "refs/peers/bob/main"
    fetched = make_cod_sync(alice, LocalFolderStore(str(bob_pub))).fetch(
        pin_to_ref=parked
    )
    assert alice.resolve_ref(parked) == fetched.observed_head
    alice.merge(parked)

    alice_files = working_tree_files(alice)
    assert alice_files == working_tree_files(bob)
    assert "todo.txt" in alice_files
    assert "notes.txt" not in alice_files
    assert "Updated by Bob." in alice_files["README.md"]
    assert_no_scratch(alice)
    assert_no_scratch(bob)


def test_an_unborn_branch_adopts_the_fetched_head(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice_pub = scratch / "alice-publication"

    alice = make_repo(scratch / "alice", "alice")
    commit_file(alice, "README.md", "# My Project\n")
    commit_file(alice, "data.txt", "Hello from Alice!\n")
    make_cod_sync(alice, make_store(alice_pub)).publish()

    bob = make_repo(scratch / "bob", "bob")
    assert not bob.has_commits()

    result = make_cod_sync(bob, LocalFolderStore(str(alice_pub))).fetch()
    bob.checkout_branch("main", result.observed_head)

    assert working_tree_files(alice) == working_tree_files(bob)
    assert set(all_refs(bob)) == {"refs/heads/main"}
