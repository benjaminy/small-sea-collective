# Test a full roundtrip through the Cod Sync bundle protocol:
#
# 1. Alice publishes, Bob clones (reuses test_clone_from_local_bundle setup)
# 2. Bob modifies, adds, and deletes files, then commits
# 3. Bob publishes an incremental bundle to his publication dir
# 4. Alice adds Bob's publication as a remote, fetches, and merges
# 5. Verify the two working trees match
#
# Exercises: push_to_remote (incremental), fetch_from_remote, merge_from_remote,
# add_remote, fetch_chain (following prerequisite links)

import pathlib

import cod_sync.protocol as CS
from test_clone_from_local_bundle import (make_cod_sync, make_file_remote,
                                          working_tree_files)


def test_roundtrip(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice_clone = scratch / "alice-clone"
    bob_clone = scratch / "bob-clone"
    alice_pub = scratch / "alice-publication"
    bob_pub = scratch / "bob-publication"

    for d in [alice_clone, bob_clone, alice_pub, bob_pub]:
        d.mkdir()

    # ---- Setup: Alice publishes, Bob clones (same as clone test) ----
    CS.gitCmd(["init", "-b", "main", str(alice_clone)])
    CS.gitCmd(["-C", str(alice_clone), "config", "user.email", "alice@test"])
    CS.gitCmd(["-C", str(alice_clone), "config", "user.name", "Alice"])

    (alice_clone / "README.md").write_text("# My Project\n")
    (alice_clone / "notes.txt").write_text("remember to buy milk\n")
    (alice_clone / "plan.txt").write_text("step 1: profit\n")
    CS.gitCmd(["-C", str(alice_clone), "add", "-A"])
    CS.gitCmd(["-C", str(alice_clone), "commit", "-m", "initial commit"])

    alice_remote = make_file_remote(alice_pub)
    alice_cod = make_cod_sync(alice_clone, "alice-pub")
    alice_cod.remote = alice_remote
    alice_cod.push_to_remote(["main"])

    bob_cod = make_cod_sync(bob_clone, "alice")
    bob_cod.clone_from_remote(f"file://{alice_pub}")
    CS.gitCmd(["-C", str(bob_clone), "config", "user.email", "bob@test"])
    CS.gitCmd(["-C", str(bob_clone), "config", "user.name", "Bob"])

    # ---- 1. Bob makes changes: modify, add, delete ----
    (bob_clone / "README.md").write_text("# My Project\n\nUpdated by Bob.\n")
    (bob_clone / "notes.txt").unlink()
    (bob_clone / "todo.txt").write_text("- write tests\n- ship it\n")
    CS.gitCmd(["-C", str(bob_clone), "add", "-A"])
    CS.gitCmd(["-C", str(bob_clone), "commit", "-m", "Bob's changes"])

    # ---- 2. Bob publishes an incremental bundle ----
    bob_remote = make_file_remote(bob_pub)
    bob_cod = make_cod_sync(bob_clone, "bob-pub")
    bob_cod.remote = bob_remote
    bob_cod.push_to_remote(["main"])

    # Verify Bob's publication has a link and bundle
    assert (bob_pub / "latest-link.yaml").exists()
    assert len(list(bob_pub.glob("B-*.bundle"))) == 1

    # ---- 3. Alice fetches and merges Bob's changes via a parked ref ----
    alice_cod = make_cod_sync(alice_clone, "bob")
    alice_cod.remote = CS.LocalFolderRemote(str(bob_pub))
    alice_cod.add_remote(f"file://{bob_pub}", [])
    parked_ref = "refs/peers/bob/main"
    fetched_sha = alice_cod.fetch_from_remote(["main"], pin_to_ref=parked_ref)
    parked_sha = CS.gitCmd(["-C", str(alice_clone), "rev-parse", parked_ref]).stdout.strip()
    assert fetched_sha == parked_sha
    alice_cod.merge_from_ref(parked_ref)

    # ---- 4. Verify the two working trees match ----
    alice_files = working_tree_files(alice_clone)
    bob_files = working_tree_files(bob_clone)

    assert alice_files == bob_files
    assert "README.md" in alice_files
    assert "todo.txt" in alice_files
    assert "plan.txt" in alice_files
    assert "notes.txt" not in alice_files
    assert "Updated by Bob." in alice_files["README.md"]


def test_merge_from_remote_bootstraps_unborn_branch(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice_clone = scratch / "alice-clone"
    bob_clone = scratch / "bob-clone"
    alice_pub = scratch / "alice-publication"

    for d in [alice_clone, bob_clone, alice_pub]:
        d.mkdir()

    CS.gitCmd(["init", "-b", "main", str(alice_clone)])
    CS.gitCmd(["-C", str(alice_clone), "config", "user.email", "alice@test"])
    CS.gitCmd(["-C", str(alice_clone), "config", "user.name", "Alice"])
    (alice_clone / "README.md").write_text("# My Project\n")
    (alice_clone / "data.txt").write_text("Hello from Alice!\n")
    CS.gitCmd(["-C", str(alice_clone), "add", "-A"])
    CS.gitCmd(["-C", str(alice_clone), "commit", "-m", "initial commit"])

    alice_remote = make_file_remote(alice_pub)
    alice_cod = make_cod_sync(alice_clone, "alice-pub")
    alice_cod.remote = alice_remote
    alice_cod.push_to_remote(["main"])

    CS.gitCmd(["init", "-b", "main", str(bob_clone)])
    CS.gitCmd(["-C", str(bob_clone), "config", "user.email", "bob@test"])
    CS.gitCmd(["-C", str(bob_clone), "config", "user.name", "Bob"])

    bob_cod = make_cod_sync(bob_clone, "alice")
    bob_cod.remote = CS.LocalFolderRemote(str(alice_pub))
    fetched_sha = bob_cod.fetch_from_remote(["main"])
    assert fetched_sha

    exit_code = bob_cod.merge_from_remote(["main"])
    assert exit_code == 0

    assert working_tree_files(alice_clone) == working_tree_files(bob_clone)
