# Test the Cod Sync bundle-based sync protocol end to end:
#
# 1. Set up scratch dirs for Alice's clone, Bob's clone,
#    and each person's bundle publication location
# 2. Alice inits a repo and commits a few tiny files
# 3. Alice publishes an initial bundle via CodSync.push_to_remote
# 4. Bob clones from Alice's publication via CodSync.clone_from_remote
# 5. Verify the two working trees match
#
# Exercises: gitCmd, CodSync.push_to_remote, CodSync.clone_from_remote,
# CodSync.build_link_blob, SmallSeaRemote link+bundle file methods,
# CodSyncRemote.read_link_blob

import os
import pathlib

import cod_sync.protocol as CS


def make_file_remote(pub_dir):
    """Create a LocalFolderRemote pointing at a local directory."""
    return CS.LocalFolderRemote(str(pub_dir))


def make_cod_sync(repo_dir, remote_name):
    """Create a CodSync wired to a specific repo directory.

    CodSync methods call self.gitCmd, but only the module-level gitCmd
    exists.  We patch it onto the instance.  CodSync also assumes cwd is
    the repo root (via change_to_root_git_dir), so we chdir there.
    """
    os.chdir(repo_dir)
    cod = CS.CodSync(remote_name)
    cod.gitCmd = CS.gitCmd
    return cod


def working_tree_files(repo_dir):
    """Return {path: content} for all git-tracked files."""
    result = CS.gitCmd(["-C", str(repo_dir), "ls-files"])
    files = {}
    for name in result.stdout.strip().splitlines():
        files[name] = (pathlib.Path(repo_dir) / name).read_text()
    return files


def test_initial_publish_and_clone(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice_clone = scratch / "alice-clone"
    bob_clone   = scratch / "bob-clone"
    alice_pub   = scratch / "alice-publication"
    bob_pub     = scratch / "bob-publication"

    for d in [alice_clone, bob_clone, alice_pub, bob_pub]:
        d.mkdir()

    # ---- 1. Alice initializes a repo and commits some files ----
    CS.gitCmd(["init", "-b", "main", str(alice_clone)])
    CS.gitCmd(["-C", str(alice_clone), "config", "user.email", "alice@test"])
    CS.gitCmd(["-C", str(alice_clone), "config", "user.name", "Alice"])

    (alice_clone / "README.md").write_text("# My Project\n")
    (alice_clone / "notes.txt").write_text("remember to buy milk\n")
    (alice_clone / "plan.txt").write_text("step 1: profit\n")
    CS.gitCmd(["-C", str(alice_clone), "add", "-A"])
    CS.gitCmd(["-C", str(alice_clone), "commit", "-m", "initial commit"])

    # ---- 2. Alice publishes an initial bundle ----
    alice_remote = make_file_remote(alice_pub)
    alice_cod = make_cod_sync(alice_clone, "alice-pub")
    alice_cod.remote = alice_remote

    alice_cod.push_to_remote(["main"])

    # Verify the publication directory has a link and a bundle
    assert (alice_pub / "latest-link.yaml").exists()
    bundles = list(alice_pub.glob("B-*.bundle"))
    assert len(bundles) == 1
    links = list(alice_pub.glob("L-*.yaml"))
    assert len(links) == 1

    # Verify we can read the link back through the protocol
    result = alice_remote.get_latest_link()
    assert result is not None
    (link, etag) = result
    assert etag is not None
    [link_ids, branches, bundle_list, supp] = link
    assert link_ids[0] == "initial-snapshot"
    assert branches[0][0] == "main"
    assert len(bundle_list) == 1
    assert supp["cod_version"] == "1.0.0"

    # ---- 3. Bob clones from Alice's publication ----
    bob_cod = make_cod_sync(bob_clone, "alice")
    bob_cod.clone_from_remote(f"file://{alice_pub}")

    CS.gitCmd(["-C", str(bob_clone), "config", "user.email", "bob@test"])
    CS.gitCmd(["-C", str(bob_clone), "config", "user.name", "Bob"])

    # ---- 4. Verify the two working trees match ----
    alice_files = working_tree_files(alice_clone)
    bob_files   = working_tree_files(bob_clone)

    assert alice_files == bob_files
    assert "README.md" in alice_files
    assert "notes.txt" in alice_files
    assert "plan.txt" in alice_files
    assert alice_files["README.md"] == "# My Project\n"
