from dataclasses import replace
import pathlib
import subprocess

import pytest
from cod_sync.store import LocalFolderStore
from ssc_files import files, sync
from test_support import local_files_signer

PARTICIPANT = "aa" * 16
PEER = "bb" * 16
TEAM = files.FilesMaterializationContext(PARTICIPANT, "11" * 16, "22" * 16, "VerifyTeam")
PEER_TEAM = files.FilesMaterializationContext(PEER, "11" * 16, "22" * 16, "VerifyTeam")


def _history(result="authorized", reason="ok"):
    def verify(_git_dir, sha):
        return {"commits": [{"commit": sha, "result": result, "reason": reason}],
                "view_identifier": "test"}
    return verify


def _setup(playground_dir):
    root = pathlib.Path(playground_dir)
    source = root / "source"
    files.init_files(str(source), PARTICIPANT)
    files.materialize_team(str(source), TEAM)
    files.create_niche(str(source), PARTICIPANT, TEAM, "docs", signer=local_files_signer(TEAM))
    source_checkout = root / "source-checkout"
    files.add_checkout(str(source), PARTICIPANT, TEAM, "docs", str(source_checkout))
    (source_checkout / "source.txt").write_text("source\n")
    files.publish(str(source), PARTICIPANT, TEAM, "docs", str(source_checkout), message="source",
                  signer=local_files_signer(TEAM))
    cloud = root / "cloud"
    cloud.mkdir()
    files.push_niche(str(source), PARTICIPANT, TEAM, "docs", LocalFolderStore(str(cloud)), signer=local_files_signer(TEAM))

    dest = root / "dest"
    files.init_files(str(dest), PEER)
    files.materialize_team(str(dest), PEER_TEAM)
    files.create_niche(str(dest), PEER, PEER_TEAM, "docs", signer=local_files_signer(PEER_TEAM))
    checkout = root / "dest-checkout"
    files.add_checkout(str(dest), PEER, PEER_TEAM, "docs", str(checkout))
    fetched = files.fetch_niche(str(dest), PEER, PEER_TEAM, "docs", PARTICIPANT,
                                LocalFolderStore(str(cloud)),
        signer=local_files_signer(PEER_TEAM),
    )
    return root, source, dest, checkout, cloud, fetched


def _verifying_signer(context, verify):
    return replace(local_files_signer(context), verify=verify)


def _head(git_dir):
    result = subprocess.run(["git", "--git-dir", str(git_dir), "rev-parse", "--verify", "HEAD"],
                            text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def test_merge_refuses_unsigned_history_and_leaves_branch_unchanged(playground_dir):
    _, _, dest, checkout, _, fetched = _setup(playground_dir)
    git_dir = files._niche_git_dir(str(dest), PEER_TEAM, "docs")
    before = _head(git_dir)
    signer = _verifying_signer(PEER_TEAM, _history("unsigned", "no signature"))
    with pytest.raises(files.UnauthorizedHistoryError) as caught:
        files.merge_niche(str(dest), PEER, PEER_TEAM, "docs", PARTICIPANT, signer=signer)
    assert caught.value.sha == fetched
    assert caught.value.refusals[0]["result"] == "unsigned"
    assert _head(git_dir) == before
    assert not (checkout / "source.txt").exists()


def test_merge_refuses_missing_authority(playground_dir):
    _, _, dest, _, _, _ = _setup(playground_dir)
    signer = _verifying_signer(PEER_TEAM, _history("missing_authority", "no authority"))
    with pytest.raises(files.UnauthorizedHistoryError) as caught:
        files.merge_niche(str(dest), PEER, PEER_TEAM, "docs", PARTICIPANT, signer=signer)
    assert caught.value.refusals[0]["result"] == "missing_authority"


def test_merge_accepts_fully_authorized_history(playground_dir):
    _, _, dest, checkout, _, fetched = _setup(playground_dir)
    signer = _verifying_signer(PEER_TEAM, _history())
    assert files.merge_niche(str(dest), PEER, PEER_TEAM, "docs", PARTICIPANT, signer=signer) == fetched
    assert (checkout / "source.txt").read_text() == "source\n"


def test_first_adopt_refuses_unauthorized_head(playground_dir):
    _, source, dest, checkout, cloud, fetched = _setup(playground_dir)
    target_git = files._niche_git_dir(str(dest), PEER_TEAM, "docs")
    subprocess.run(["git", "--git-dir", str(target_git), "update-ref", "-d", "refs/peers/" + PARTICIPANT + "/main"], check=True)
    # A fresh repository exercises _cod_pull's unborn branch adoption.
    target_git2 = pathlib.Path(playground_dir) / "unborn.git"
    target_git2.mkdir()
    files._init_git_dir(target_git2)
    checkout2 = pathlib.Path(playground_dir) / "unborn-checkout"
    checkout2.mkdir()
    with pytest.raises(files.UnauthorizedHistoryError):
        files._cod_pull(target_git2, checkout2, LocalFolderStore(str(cloud)),
                        _verifying_signer(PEER_TEAM, _history("bad_signature", "bad sig")))
    assert subprocess.run(["git", "--git-dir", str(target_git2), "rev-parse", "--verify", "HEAD"],
                          capture_output=True).returncode != 0
    assert not (checkout2 / "source.txt").exists()


def test_merge_self_refuses_unauthorized_parked_head(playground_dir, monkeypatch):
    _, _, dest, checkout, _, fetched = _setup(playground_dir)
    git_dir = files._niche_git_dir(str(dest), PEER_TEAM, "docs")
    # Move the fetched peer head to the self-store parked namespace.
    from cod_sync.protocol import parked_ref_name
    ref = parked_ref_name("self")
    subprocess.run(["git", "--git-dir", str(git_dir), "update-ref", ref, fetched], check=True)
    monkeypatch.setattr(files.CS, "outstanding_parked_heads", lambda _repo: [(ref, fetched)])
    with pytest.raises(files.UnauthorizedHistoryError):
        files._merge_parked_self_refs(
            git_dir, checkout,
            _verifying_signer(PEER_TEAM, _history("wrong_scope", "wrong scope")),
        )
    assert _head(git_dir) is None


def test_merge_uses_checked_sha_not_moved_ref(playground_dir):
    _, _, dest, checkout, _, fetched = _setup(playground_dir)
    git_dir = files._niche_git_dir(str(dest), PEER_TEAM, "docs")
    extra = pathlib.Path(playground_dir) / "alternate"
    extra.mkdir()
    subprocess.run(["git", "init", str(extra)], check=True, capture_output=True)
    (extra / "alternate.txt").write_text("alternate\n")
    subprocess.run(["git", "-C", str(extra), "-c", "user.name=T", "-c", "user.email=t@t",
                    "add", "."], check=True)
    subprocess.run(["git", "-C", str(extra), "-c", "user.name=T", "-c", "user.email=t@t",
                    "commit", "-m", "alternate"], check=True, capture_output=True)
    alternate = subprocess.check_output(["git", "-C", str(extra), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "--git-dir", str(git_dir), "fetch", str(extra), alternate], check=True, capture_output=True)
    ref = files._peer_ref_name(PARTICIPANT)
    def move_during_verify(_git_dir, sha):
        assert sha == fetched
        subprocess.run(["git", "--git-dir", str(git_dir), "update-ref", ref, alternate], check=True)
        return {"commits": [{"commit": sha, "result": "authorized", "reason": "ok"}], "view_identifier": "test"}
    files.merge_niche(str(dest), PEER, PEER_TEAM, "docs", PARTICIPANT,
                      signer=_verifying_signer(PEER_TEAM, move_during_verify))
    assert _head(git_dir) == fetched
    assert (checkout / "source.txt").exists()
    assert not (checkout / "alternate.txt").exists()


def test_refusal_leaves_parked_ref_in_place(playground_dir):
    _, _, dest, _, _, fetched = _setup(playground_dir)
    git_dir = files._niche_git_dir(str(dest), PEER_TEAM, "docs")
    ref = files._peer_ref_name(PARTICIPANT)
    with pytest.raises(files.UnauthorizedHistoryError):
        files.merge_niche(str(dest), PEER, PEER_TEAM, "docs", PARTICIPANT,
                          signer=_verifying_signer(PEER_TEAM, _history("ambiguous_authority", "ambiguous")))
    assert files._resolve_ref(git_dir, ref) == fetched


def test_sync_merge_via_hub_surfaces_refusal(playground_dir, monkeypatch):
    _, _, dest, _, _, _ = _setup(playground_dir)
    monkeypatch.setattr(sync, "resolve_team_context", lambda *_: PEER_TEAM)
    monkeypatch.setattr(sync, "commit_signer", lambda *_args, **_kwargs:
                        _verifying_signer(PEER_TEAM, _history("unsigned", "no signature")))
    with pytest.raises(sync.UnauthorizedHistoryError, match="1 commit.*unsigned: no signature"):
        sync.merge_via_hub(str(dest), PEER, "VerifyTeam", "docs", PARTICIPANT)


@pytest.mark.parametrize("operation", ["fetch", "fetch_self", "publish"])
@pytest.mark.parametrize("verdict", ["unsigned", "bad_signature", "missing_authority"])
def test_files_transfer_refuses_unaccepted_history(playground_dir, operation, verdict):
    from cod_sync.repo import Repo

    root, source, dest, _, cloud, _ = _setup(playground_dir)
    source_git = files._niche_git_dir(str(source), TEAM, "docs")
    dest_git = files._niche_git_dir(str(dest), PEER_TEAM, "docs")
    before = Repo(dest_git).list_refs("refs/")
    signer = _verifying_signer(PEER_TEAM, _history(verdict, "refused"))
    with pytest.raises(files.UnauthorizedHistoryError):
        if operation == "fetch":
            files._cod_fetch(dest_git, LocalFolderStore(str(cloud)), "refs/peers/new/main", signer)
        elif operation == "fetch_self":
            files._cod_fetch_self(dest_git, LocalFolderStore(str(cloud)), signer)
        else:
            unpublished = root / "unpublished"
            unpublished.mkdir()
            files._cod_push(source_git, LocalFolderStore(str(unpublished)), signer)
    assert Repo(dest_git).list_refs("refs/") == before
    if operation == "publish":
        assert not list(unpublished.iterdir())
