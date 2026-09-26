"""Micro tests for the signed work scope."""

import base64
import json
import pathlib

import pytest

from cod_sync.verify import SshCommitVerifier
from cod_sync.work_context import (
    WorkContext, WorkContextError, check_work_context, commit_message_with_context,
    context_from_commit, decode_work_context, encode_work_context,
)
from cod_sync_test_helpers import (
    commit_file, isolated_git_config, make_repo, make_ssh_key, public_key_text,
)

pytestmark = pytest.mark.usefixtures("isolated_git_config")


def _context(**changes):
    values = dict(origin="commit", team_id="team", berth_id="berth-a",
                  purpose="files-content", authority_view=b"\x00view\xff")
    values.update(changes)
    return WorkContext(**values)


def _token(payload):
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def test_work_context_round_trip():
    for origin in ("commit", "publication-link"):
        context = _context(origin=origin)
        assert decode_work_context(encode_work_context(context)) == context
        assert encode_work_context(decode_work_context(encode_work_context(context))) == encode_work_context(context)


def test_work_context_rejects_duplicate_missing_unknown_version():
    valid = json.loads(base64.urlsafe_b64decode(encode_work_context(_context()) + "=="))
    missing = dict(valid)
    del missing["berth_id"]
    future = dict(valid, version=2)
    duplicate = json.dumps(valid)[:-1] + ',"purpose":"core"}'
    for payload in (json.dumps(missing), json.dumps(future), duplicate):
        with pytest.raises(WorkContextError):
            decode_work_context(_token(payload))


def test_work_context_rejects_cross_berth_and_cross_purpose(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = make_ssh_key(root, "signer")
    repo = make_repo(root / "repo")
    repo.configure_signing(key)
    context = _context()
    sha = commit_file(repo, "a.txt", "a", commit_message_with_context("write a", context))
    assert SshCommitVerifier([public_key_text(key)]).verify_history(repo, sha)
    actual = context_from_commit(repo, sha)
    check_work_context(actual, origin="commit", team_id="team", berth_id="berth-a",
                       purpose="files-content", authority_view=b"\x00view\xff")
    for berth, purpose in (("berth-b", "files-content"), ("berth-a", "files-registry")):
        with pytest.raises(WorkContextError):
            check_work_context(actual, origin="commit", team_id="team", berth_id=berth,
                               purpose=purpose, authority_view=b"\x00view\xff")


def test_old_basis_is_not_creation_time(scratch_dir):
    root = pathlib.Path(scratch_dir)
    key = make_ssh_key(root, "signer")
    repo = make_repo(root / "repo")
    repo.configure_signing(key)
    repo.config("user.email", "alice@test")
    context = _context(authority_view=b"old-view")
    sha = commit_file(repo, "a.txt", "a", commit_message_with_context("write a", context))
    assert SshCommitVerifier([public_key_text(key)]).verify_history(repo, sha)
    assert context_from_commit(repo, sha).authority_view == b"old-view"
    assert "timestamp" not in decode_work_context(encode_work_context(context)).__dict__
    check_work_context(context_from_commit(repo, sha), origin="commit", team_id="team",
                       berth_id="berth-a", purpose="files-content", authority_view=b"old-view")
