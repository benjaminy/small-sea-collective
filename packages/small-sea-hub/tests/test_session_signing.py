"""Session-scoped workhorse signing micro tests."""

import base64
from datetime import datetime, timedelta, timezone
import pathlib
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cod_sync.work_context import WorkContext, encode_work_context
from small_sea_hub.backend import SmallSeaBackend, SmallSeaSession
from small_sea_hub.server import app
import small_sea_manager.provisioning as provisioning
from small_sea_manager.berth_authority import MissingAuthorityAnchor


@pytest.fixture()
def signing_env(playground_dir):
    backend = SmallSeaBackend(root_dir=playground_dir)
    participant = provisioning.create_new_participant(playground_dir, "alice")
    app.state.backend = backend
    token = backend.open_session("alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests").hex()
    session = backend._lookup_session(token)
    client = TestClient(app)
    return backend, client, participant, token, session


def _payload(session, **changes):
    fields = dict(origin="commit", team_id=session.team_id.hex(), berth_id=session.berth_id.hex(),
                  purpose="note-to-self", authority_view=b"view")
    fields.update(changes)
    message = "message\n\nSmall-Sea-Work-Context: " + encode_work_context(WorkContext(**fields)) + "\n"
    return (b"tree " + b"0" * 40 + b"\nauthor Alice <alice@test> 1 +0000\n"
            b"committer Alice <alice@test> 1 +0000\n\n" + message.encode())


def _sign(client, token, payload, purpose="note-to-self"):
    return client.post("/session/sign", headers={"Authorization": f"Bearer {token}"},
                       json={"purpose": purpose, "payload": base64.b64encode(payload).decode()})


def _files_session(backend, client, participant):
    provisioning.create_team(backend.root_dir, participant, "FilesTests")
    provisioning.register_app_for_participant(
        backend.root_dir, participant, "SmallSeaCollectiveFiles"
    )
    provisioning.activate_app_for_team(
        backend.root_dir, participant, "FilesTests",
        "SmallSeaCollectiveFiles",
    )
    token = backend.open_session(
        "alice", "SmallSeaCollectiveFiles", "FilesTests", "Smoke Tests"
    ).hex()
    session = backend._lookup_session(token)
    provisioning.get_workhorse_signing_key(backend.root_dir, participant, session.berth_id)
    return token, session


def test_sign_accepts_own_app_prefix(signing_env):
    backend, client, participant, _token, _session = signing_env
    token, session = _files_session(backend, client, participant)
    purpose = "SmallSeaCollectiveFiles/content"
    response = _sign(client, token, _payload(session, purpose=purpose), purpose)
    assert response.status_code == 200, response.text


def test_sign_refuses_other_app_prefix(signing_env):
    backend, client, participant, _token, _session = signing_env
    token, session = _files_session(backend, client, participant)
    purpose = "AnotherApp/content"
    response = _sign(client, token, _payload(session, purpose=purpose), purpose)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "purpose_not_allowed"


def test_sign_refuses_unprefixed_app_label(signing_env):
    backend, client, participant, _token, _session = signing_env
    token, session = _files_session(backend, client, participant)
    purpose = "content"
    payload = _payload(session, purpose="SmallSeaCollectiveFiles/content")
    response = _sign(client, token, payload, purpose)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "purpose_not_allowed"


def test_session_authority_view(signing_env, monkeypatch):
    backend, client, participant, _token, _session = signing_env
    provisioning.create_team(backend.root_dir, participant, "ProjectX")
    token = backend.open_session("alice", "SmallSeaCollectiveCore", "ProjectX", "Smoke Tests").hex()
    response = client.get("/session/authority_view", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    encoded = response.json()["authority_view"]
    assert "=" not in encoded
    assert base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)) == (
        provisioning.load_transitional_authority_view(backend.root_dir, participant, "ProjectX").identifier
    )

    def absent(*args):
        raise MissingAuthorityAnchor("no adopted anchor")

    monkeypatch.setattr(provisioning, "load_transitional_authority_view", absent)
    missing = client.get("/session/authority_view", headers={"Authorization": f"Bearer {token}"})
    assert missing.status_code == 409
    assert missing.json()["detail"] == {"code": "authority_anchor_absent"}


def test_signing_uses_session_scope(signing_env):
    backend, client, participant, token, session = signing_env
    provisioning.get_workhorse_signing_key(backend.root_dir, participant, session.berth_id)
    valid = _payload(session)
    variants = [
        (_payload(session, berth_id="ab" * 16), "note-to-self", token, 400, "invalid_commit"),
        (_payload(session, team_id="ab" * 16), "note-to-self", token, 400, "invalid_commit"),
        (_payload(session, origin="publication-link"), "note-to-self", token, 400, "invalid_commit"),
        (valid, "core", token, 403, "purpose_not_allowed"),
        (valid.rsplit(b"Small-Sea-Work-Context:", 1)[0], "note-to-self", token, 400, "invalid_commit"),
        (valid + valid[valid.index(b"Small-Sea-Work-Context:"):], "note-to-self", token, 400, "invalid_commit"),
        (valid.replace(b"author ", b"gpgsig x\nauthor "), "note-to-self", token, 400, "invalid_commit"),
        (b"bad", "note-to-self", token, 400, "invalid_commit"),
        (valid, "note-to-self", "ff" * 32, 401, None),
    ]
    for payload, purpose, bearer, status, code in variants:
        response = _sign(client, bearer, payload, purpose)
        assert response.status_code == status, response.text
        if code:
            assert response.json()["detail"]["code"] == code
        else:
            assert response.json()["detail"].startswith("Session not found")
    malformed = client.post("/session/sign", headers={"Authorization": f"Bearer {token}"},
                            json={"purpose": "note-to-self", "payload": "!!"})
    assert malformed.status_code == 400
    assert malformed.json()["detail"]["code"] == "invalid_commit"
    provisioning.create_team(backend.root_dir, participant, "ProjectX")
    team_token = backend.open_session("alice", "SmallSeaCollectiveCore", "ProjectX", "Smoke Tests").hex()
    team_session = backend._lookup_session(team_token)
    provisioning.get_workhorse_signing_key(backend.root_dir, participant, team_session.berth_id)
    assert _sign(client, team_token, _payload(team_session, purpose="core"), "core").status_code == 200
    denied = _sign(client, team_token, _payload(team_session), "note-to-self")
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "purpose_not_allowed"
    engine = create_engine(f"sqlite:///{backend.path_local_db}")
    with Session(engine) as db:
        row = db.query(SmallSeaSession).filter_by(token=bytes.fromhex(token)).one()
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        row.duration_sec = 1
        db.commit()
    expired = _sign(client, token, valid)
    assert expired.status_code == 401
    assert expired.json()["detail"].startswith("Session not found")


def test_signing_exports_no_secret(signing_env, tmp_path):
    backend, client, participant, token, session = signing_env
    secret = provisioning.get_workhorse_signing_key(backend.root_dir, participant, session.berth_id)
    payload = _payload(session)
    response = _sign(client, token, payload)
    assert response.status_code == 200, response.text
    result = response.json()
    key_response = client.get("/session/signing_key", headers={"Authorization": f"Bearer {token}"})
    assert key_response.status_code == 200
    assert key_response.json() == {"public_key": result["public_key"]}
    for form in (secret, secret.hex().encode(), base64.b64encode(secret)):
        assert form not in response.content
        assert form not in key_response.content
    key = result["public_key"]
    signature = result["signature"]
    signers = tmp_path / "signers"
    signers.write_text(f"alice@test {key}\n")
    sigfile = tmp_path / "signature"
    sigfile.write_text(signature)
    verify = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", "alice@test",
                             "-n", "git", "-s", str(sigfile)], input=payload, capture_output=True)
    assert verify.returncode == 0, verify.stderr
    tampered = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", "alice@test",
                              "-n", "git", "-s", str(sigfile)], input=payload + b"x", capture_output=True)
    assert tampered.returncode != 0
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    lines = signature.rstrip("\n").split("\n")
    signed = payload.replace(b"\n\n", ("\ngpgsig " + lines[0] + "\n" +
                            "\n".join(" " + line for line in lines[1:]) + "\n\n").encode(), 1)
    sha = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                         input=signed, capture_output=True, check=True).stdout.decode().strip()
    git_verify = subprocess.run(["git", "-C", str(repo), "-c", "gpg.format=ssh",
                                 "-c", f"gpg.ssh.allowedSignersFile={signers}", "verify-commit", sha],
                                capture_output=True)
    assert git_verify.returncode == 0, git_verify.stderr


def test_signing_key_absent_is_not_created(signing_env):
    backend, client, participant, token, session = signing_env
    before = list(pathlib.Path(backend.root_dir).rglob("workhorse-*.key"))
    response = _sign(client, token, _payload(session))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "signing_key_absent"
    assert list(pathlib.Path(backend.root_dir).rglob("workhorse-*.key")) == before


def test_session_expiry_and_created_at(signing_env):
    backend, client, participant, token, session = signing_env
    other = backend.open_session("alice", "SmallSeaCollectiveCore", "NoteToSelf", "Smoke Tests").hex()
    engine = create_engine(f"sqlite:///{backend.path_local_db}")
    with Session(engine) as db:
        first = db.query(SmallSeaSession).filter_by(token=bytes.fromhex(token)).one()
        second = db.query(SmallSeaSession).filter_by(token=bytes.fromhex(other)).one()
        assert first.created_at != second.created_at
        first.created_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        first.duration_sec = 1
        db.commit()
    assert client.get("/session/info", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/session/info", headers={"Authorization": f"Bearer {other}"}).status_code == 200
