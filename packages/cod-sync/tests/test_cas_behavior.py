# Test CAS (compare-and-swap) behavior on LocalFolderRemote.
#
# Exercises: CAS conflict detection, CAS success with correct etag,
# and cod_version field roundtrip.

import pathlib

import yaml
import pytest

import cod_sync.protocol as CS

from test_clone_from_local_bundle import (
    make_file_remote,
    make_cod_sync,
)


def test_cas_success_local(scratch_dir):
    """Two sequential pushes with correct etags both succeed."""
    scratch = pathlib.Path(scratch_dir)
    repo = scratch / "repo"
    pub = scratch / "publication"
    repo.mkdir()
    pub.mkdir()

    CS.gitCmd(["init", "-b", "main", str(repo)])
    CS.gitCmd(["-C", str(repo), "config", "user.email", "alice@test"])
    CS.gitCmd(["-C", str(repo), "config", "user.name", "Alice"])

    (repo / "file1.txt").write_text("hello\n")
    CS.gitCmd(["-C", str(repo), "add", "-A"])
    CS.gitCmd(["-C", str(repo), "commit", "-m", "first"])

    remote = make_file_remote(pub)
    cod = make_cod_sync(repo, "pub")
    cod.remote = remote

    # First push (no existing latest-link.yaml, etag=None)
    cod.push_to_remote(["main"])
    assert (pub / "latest-link.yaml").exists()

    # Read back etag
    (link1, etag1) = remote.get_latest_link()
    assert etag1 is not None

    # Second push
    (repo / "file2.txt").write_text("world\n")
    CS.gitCmd(["-C", str(repo), "add", "-A"])
    CS.gitCmd(["-C", str(repo), "commit", "-m", "second"])

    cod2 = make_cod_sync(repo, "pub")
    cod2.remote = remote
    cod2.push_to_remote(["main"])

    # Verify chain grew
    (link2, etag2) = remote.get_latest_link()
    assert etag2 is not None
    assert etag2 != etag1
    assert link2[0][1] == link1[0][0]  # prev_link_uid points to first link


def test_cas_conflict_local(scratch_dir):
    """Pushing with a stale etag raises CasConflictError."""
    scratch = pathlib.Path(scratch_dir)
    repo = scratch / "repo"
    pub = scratch / "publication"
    repo.mkdir()
    pub.mkdir()

    CS.gitCmd(["init", "-b", "main", str(repo)])
    CS.gitCmd(["-C", str(repo), "config", "user.email", "alice@test"])
    CS.gitCmd(["-C", str(repo), "config", "user.name", "Alice"])

    (repo / "file1.txt").write_text("hello\n")
    CS.gitCmd(["-C", str(repo), "add", "-A"])
    CS.gitCmd(["-C", str(repo), "commit", "-m", "first"])

    remote = make_file_remote(pub)
    cod = make_cod_sync(repo, "pub")
    cod.remote = remote
    cod.push_to_remote(["main"])

    # Get current etag
    (link, etag) = remote.get_latest_link()
    assert etag is not None

    # Tamper with latest-link.yaml to simulate a concurrent write
    latest_path = pub / "latest-link.yaml"
    with open(latest_path, "a") as f:
        f.write("# tampered\n")

    # Now try to upload with the stale etag — should fail
    blob = cod.build_link_blob("test-uid", link[0][0], "bundle-uid", {"main": "initial-snapshot"})
    bundle_path = pub / list(pub.glob("B-*.bundle"))[0].name

    with pytest.raises(CS.CasConflictError):
        remote.upload_latest_link("test-uid", blob, "bundle-uid", str(bundle_path), expected_etag=etag)


def test_version_field_roundtrip(scratch_dir):
    """Push and read back — verify cod_version is present and correct."""
    scratch = pathlib.Path(scratch_dir)
    repo = scratch / "repo"
    pub = scratch / "publication"
    repo.mkdir()
    pub.mkdir()

    CS.gitCmd(["init", "-b", "main", str(repo)])
    CS.gitCmd(["-C", str(repo), "config", "user.email", "alice@test"])
    CS.gitCmd(["-C", str(repo), "config", "user.name", "Alice"])

    (repo / "file1.txt").write_text("hello\n")
    CS.gitCmd(["-C", str(repo), "add", "-A"])
    CS.gitCmd(["-C", str(repo), "commit", "-m", "first"])

    remote = make_file_remote(pub)
    cod = make_cod_sync(repo, "pub")
    cod.remote = remote
    cod.push_to_remote(["main"])

    (link, etag) = remote.get_latest_link()
    [link_ids, branches, bundles, supp] = link
    assert "cod_version" in supp
    assert supp["cod_version"] == CS.COD_SYNC_VERSION
    assert supp["cod_version"] == "1.0.0"

    # Also verify the archived L-{uid}.yaml has the same content
    archived = remote.get_link(link_ids[0])
    assert archived is not None
    [_, _, _, archived_supp] = archived
    assert archived_supp["cod_version"] == "1.0.0"
