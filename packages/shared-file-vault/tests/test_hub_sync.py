import base64
import json
import pathlib
import socket

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from click.testing import CliRunner
from fastapi.testclient import TestClient
from shared_file_vault import sync, vault
from shared_file_vault.cli import cli
from small_sea_client.client import SmallSeaClient, SmallSeaSession
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager, _CORE_APP


def _open_session(http, nickname, team, mode="encrypted", app_name=sync.HUB_APP_NAME):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": app_name,
            "team": team,
            "client": "Smoke Tests",
            "mode": mode,
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _read_s3_object(endpoint, access_key, secret_key, bucket_name, key):
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    return s3.get_object(Bucket=bucket_name, Key=key)["Body"].read()


def _push_team_repo_via_hub(http, session_hex, repo_dir):
    auth = {"Authorization": f"Bearer {session_hex}"}
    resp = http.post("/cloud/setup", headers=auth)
    assert resp.status_code == 200, resp.text
    _publish_storage_announcement_for_session(app.state.backend, session_hex)

    from cod_sync.protocol import CodSync, SmallSeaRemote

    remote = SmallSeaRemote(session_hex, base_url="http://testserver", client=http)
    cs = CodSync("origin", repo_dir=pathlib.Path(repo_dir))
    cs.remote = remote
    cs.push_to_remote(["main"])


def _publish_storage_announcement_for_session(backend, session_hex):
    ss_session = backend._lookup_session(session_hex)
    if ss_session.team_name == "NoteToSelf":
        return
    allocation = Provisioning.get_berth_cloud_allocation_for_berth(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.berth_id,
    )
    assert allocation is not None
    _team_id, self_member_id = Provisioning._team_row(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
    )
    Provisioning.publish_member_berth_storage_announcement(
        backend.root_dir,
        ss_session.participant_id.hex(),
        ss_session.team_name,
        self_member_id,
        ss_session.berth_id,
        allocation,
    )


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _session_berth_info(http, session_hex):
    return http.get(
        "/session/info",
        headers={"Authorization": f"Bearer {session_hex}"},
    ).json()


def _setup_two_member_team(playground_dir, minio_server_gen):
    alice_minio = minio_server_gen(port=_free_port())
    bob_minio = minio_server_gen(port=_free_port())
    root = pathlib.Path(playground_dir)

    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")
    Provisioning.register_app_for_participant(root, alice_hex, sync.HUB_APP_NAME)
    Provisioning.register_app_for_participant(root, bob_hex, sync.HUB_APP_NAME)

    alice_nts = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    alice_cloud_id = backend.add_cloud_location(
        alice_nts,
        "s3",
        alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf", mode="passthrough")
    bob_cloud_id = backend.add_cloud_location(
        bob_nts,
        "s3",
        bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")
    Provisioning.activate_app_for_team(root, alice_hex, "ProjectX", sync.HUB_APP_NAME)
    alice_member_id_hex = team_result["member_id_hex"]

    alice_team_token = _open_session(http, "Alice", "ProjectX")
    alice_vault_berth = _session_berth_info(http, alice_team_token)["berth_id"]
    # Keep this fixture's public bucket stable so older peer-sync assertions
    # can inspect exactly where Alice's app berth writes.
    team_bucket = f"ss-{alice_vault_berth[:16]}"
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root,
        alice_hex,
        alice_vault_berth,
        alice_cloud_id,
        location=team_bucket,
    )
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    resp = http.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {alice_team_token}"},
    )
    assert resp.status_code == 200, resp.text
    _publish_storage_announcement_for_session(backend, alice_team_token)

    alice_core_team_token = _open_session(http, "Alice", "ProjectX", app_name=_CORE_APP)
    _push_team_repo_via_hub(http, alice_core_team_token, alice_team_sync)

    token_b64 = Provisioning.create_invitation(
        root,
        alice_hex,
        "ProjectX",
        {"protocol": "s3", "url": alice_minio["endpoint"]},
        invitee_label="Bob",
    )
    _push_team_repo_via_hub(http, alice_core_team_token, alice_team_sync)

    bob_manager = TeamManager(root, bob_hex, _http_client=http)
    acceptance_b64 = bob_manager.accept_invitation(token_b64)
    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    bob_member_id_hex = acceptance["acceptor_member_id"]
    bob_team_token = _open_session(http, "Bob", "ProjectX")
    bob_vault_berth = _session_berth_info(http, bob_team_token)["berth_id"]
    # Match Alice's stable fixture shape for Bob's own app berth.
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root,
        bob_hex,
        bob_vault_berth,
        bob_cloud_id,
        location=f"ss-{bob_vault_berth[:16]}",
    )
    resp = http.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {bob_team_token}"},
    )
    assert resp.status_code == 200, resp.text
    _publish_storage_announcement_for_session(backend, bob_team_token)
    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)

    return {
        "root": root,
        "http": http,
        "alice_minio": alice_minio,
        "alice_hex": alice_hex,
        "bob_hex": bob_hex,
        "alice_member_id_hex": alice_member_id_hex,
        "bob_member_id_hex": bob_member_id_hex,
        "team_bucket": team_bucket,
    }


def test_signal_watermark_roundtrip(tmp_path, monkeypatch):
    root = str(tmp_path / "vault")
    participant = "aa" * 16
    vault.init_vault(root, participant)
    team_a = vault.VaultMaterializationContext(participant, "11" * 16, "TeamA")
    team_b = vault.VaultMaterializationContext(participant, "22" * 16, "TeamB")
    team_c = vault.VaultMaterializationContext(participant, "33" * 16, "TeamC")

    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 0

    sync.set_signal_watermark(root, participant, team_a, "aa" * 16, 5)
    sync.set_signal_watermark(root, participant, team_a, "bb" * 16, 2)
    sync.set_signal_watermark(root, participant, team_b, "aa" * 16, 9)

    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 5
    assert sync.get_signal_watermark(root, participant, team_a, "bb" * 16) == 2
    assert sync.get_signal_watermark(root, participant, team_b, "aa" * 16) == 9
    # Unrelated team/member still 0
    assert sync.get_signal_watermark(root, participant, team_c, "cc" * 16) == 0

    sync.clear_signal_watermark(root, participant, team_a, "aa" * 16)
    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 0
    # Other entries untouched
    assert sync.get_signal_watermark(root, participant, team_a, "bb" * 16) == 2


def test_signal_watermark_persists_alongside_session_token(tmp_path, monkeypatch):
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))

    sync.save_config(
        {
            "vault_root": str(tmp_path / "v"),
            "team_sessions": {"TeamA": {"session_token": "tok"}},
        }
    )
    root = str(tmp_path / "v")
    participant = "aa" * 16
    team_a = vault.VaultMaterializationContext(participant, "11" * 16, "TeamA")
    vault.init_vault(root, participant)
    sync.set_signal_watermark(root, participant, team_a, "aa" * 16, 7)

    loaded = sync.load_config()
    assert loaded["team_sessions"]["TeamA"]["session_token"] == "tok"
    assert "peer_signal_watermarks" not in loaded
    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 7


def test_peer_update_status_has_unfetched_hint(tmp_path, monkeypatch, playground_dir):
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))

    root = playground_dir
    participant = "bb" * 16
    member_id = "cc" * 16
    team = vault.VaultMaterializationContext(participant, "44" * 16, "HintTeam")
    niche = "files"

    vault.init_vault(root, participant)
    vault.materialize_team(root, team)
    vault.create_niche(root, participant, team, niche)

    # No watermark set → current 3 > watermark 0 → hint True
    status = sync.peer_update_status(root, participant, team, niche, member_id,
                                     current_signal_count=3)
    assert status.has_unfetched_hint is True
    assert status.current_signal_count == 3
    assert status.last_seen_signal_count == 0

    # Set watermark to match → hint False
    sync.set_signal_watermark(root, participant, team, member_id, 3)
    status = sync.peer_update_status(root, participant, team, niche, member_id,
                                     current_signal_count=3)
    assert status.has_unfetched_hint is False

    # current 0, watermark 0 → hint False (never pushed)
    status = sync.peer_update_status(root, participant, team, niche, "dd" * 16,
                                     current_signal_count=0)
    assert status.has_unfetched_hint is False


def test_fetch_via_hub_advances_watermark(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_member_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_vault_root = str(root / "vault-alice")
    bob_vault_root = str(root / "vault-bob")
    vault.init_vault(alice_vault_root, env["alice_hex"])
    vault.init_vault(bob_vault_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    alice_login = sync.login_team(alice_vault_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = vault.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    vault.create_niche(alice_vault_root, env["alice_hex"], alice_context, "docs")
    vault.add_checkout(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "file.txt").write_text("hello\n")
    vault.publish(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="init")

    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    bob_token = sync.login_team(bob_vault_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = vault.materialization_context_from_session_info(bob_token.session_info)

    # Seed peer_counts so /session/peers reports Alice's signal as 5.
    # The background watcher is not running in TestClient, so we set it directly.
    bob_session = SmallSeaSession(SmallSeaClient(port=11437, _http_client=http), bob_token.session_token)
    bob_berth_id_hex = bob_session.session_info()["berth_id"]
    if not hasattr(app.state, "peer_counts"):
        app.state.peer_counts = {}
    app.state.peer_counts[(bob_berth_id_hex, env["alice_member_id_hex"])] = 5

    assert sync.get_signal_watermark(
        bob_vault_root, env["bob_hex"], bob_context, env["alice_member_id_hex"]
    ) == 0

    sync.fetch_via_hub(
        bob_vault_root, env["bob_hex"], "ProjectX", "docs",
        env["alice_member_id_hex"], _http_client=http,
    )

    # Watermark should equal the observed signal_count (5)
    assert sync.get_signal_watermark(
        bob_vault_root, env["bob_hex"], bob_context, env["alice_member_id_hex"]
    ) == 5


def test_fetch_via_hub_does_not_touch_other_peers_watermark(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_member_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_vault_root = str(root / "vault-alice")
    bob_vault_root = str(root / "vault-bob")
    vault.init_vault(alice_vault_root, env["alice_hex"])
    vault.init_vault(bob_vault_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    alice_login = sync.login_team(alice_vault_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = vault.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    vault.create_niche(alice_vault_root, env["alice_hex"], alice_context, "docs")
    vault.add_checkout(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "file.txt").write_text("hello\n")
    vault.publish(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="init")

    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    bob_login = sync.login_team(bob_vault_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = vault.materialization_context_from_session_info(bob_login.session_info)

    # Plant a watermark for a different fake peer
    other_member = "ff" * 16
    sync.set_signal_watermark(bob_vault_root, env["bob_hex"], bob_context, other_member, 99)

    sync.fetch_via_hub(
        bob_vault_root, env["bob_hex"], "ProjectX", "docs",
        env["alice_member_id_hex"], _http_client=http,
    )

    # Other peer's watermark is untouched
    assert sync.get_signal_watermark(bob_vault_root, env["bob_hex"], bob_context, other_member) == 99


def test_sync_config_roundtrip_and_remote_prefixes(tmp_path, monkeypatch):
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))

    sync.save_config(
        {
            "vault_root": "/tmp/vault",
            "participant_hex": "aa" * 16,
            "hub_port": 12345,
            "team_sessions": {"Project X": {"session_token": "tok-123"}},
        }
    )

    loaded = sync.load_config()
    assert loaded["vault_root"] == "/tmp/vault"
    assert loaded["participant_hex"] == "aa" * 16
    assert loaded["hub_port"] == 12345
    assert loaded["team_sessions"]["Project X"]["session_token"] == "tok-123"

    session = SmallSeaSession(SmallSeaClient(port=7777), "session-token")
    registry_remote = sync.make_registry_remote(session)
    niche_remote = sync.make_niche_remote("docs", session)

    assert registry_remote._path_prefix == "registry/"
    assert niche_remote._path_prefix == "niches/docs/"


def test_login_team_pin_flow_persists_token(playground_dir, monkeypatch):
    root = pathlib.Path(playground_dir)
    config_file = root / "alice-vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))

    backend = SmallSea.SmallSeaBackend(root_dir=str(root))
    app.state.backend = backend
    http = TestClient(app)

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.register_app_for_participant(root, alice_hex, sync.HUB_APP_NAME)
    Provisioning.create_team(root, alice_hex, "ProjectX")
    Provisioning.activate_app_for_team(root, alice_hex, "ProjectX", sync.HUB_APP_NAME)

    captured = {}
    original = backend.request_session

    def _capturing(participant, app_name, team, client_name, mode="encrypted"):
        pending_id, pin = original(participant, app_name, team, client_name, mode=mode)
        captured["pin"] = pin
        return pending_id, pin

    backend.request_session = _capturing
    alice_vault_root = str(root / "vault-alice")
    try:
        result = sync.login_team(
            alice_vault_root,
            "ProjectX",
            alice_hex,
            _http_client=http,
            pin_reader=lambda _: captured["pin"],
        )
    finally:
        backend.request_session = original

    assert result.auto_approved is False
    assert result.session_info["team_name"] == "ProjectX"
    assert sync.load_config()["team_sessions"]["ProjectX"]["session_token"] == result.session_token


def test_cli_push_uses_config_defaults(monkeypatch, tmp_path):
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))
    sync.save_config(
        {
            "vault_root": "/tmp/example-vault",
            "participant_hex": "aa" * 16,
            "hub_port": 23456,
        }
    )

    captured = {}

    def _fake_push(vault_root, participant_hex, team_name, niche_name, *, hub_port, _http_client=None):
        captured.update(
            {
                "vault_root": vault_root,
                "participant_hex": participant_hex,
                "team_name": team_name,
                "niche_name": niche_name,
                "hub_port": hub_port,
            }
        )

    monkeypatch.setattr(sync, "push_via_hub", _fake_push)
    runner = CliRunner()
    result = runner.invoke(cli, ["push", "ProjectX", "docs"])

    assert result.exit_code == 0, result.output
    assert captured == {
        "vault_root": "/tmp/example-vault",
        "participant_hex": "aa" * 16,
        "team_name": "ProjectX",
        "niche_name": "docs",
        "hub_port": 23456,
    }


def test_cli_local_commands_resolve_offline_from_metadata(monkeypatch, tmp_path):
    """Local CLI commands resolve friendly team_name to team_id from metadata.json.

    No Hub call is required: once a team has been materialized (via login or
    test-direct vault.materialize_team), subsequent local operations read the
    team_id from metadata.json offline.
    """
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))
    participant = "aa" * 16
    team_id = "11" * 16
    vault_root = tmp_path / "vault"
    vault.init_vault(str(vault_root), participant)
    vault.materialize_team(
        str(vault_root),
        vault.VaultMaterializationContext(participant, team_id, "ProjectX"),
    )

    def _fail(*_a, **_kw):
        raise AssertionError("local CLI commands must not contact the Hub")

    monkeypatch.setattr(sync, "get_team_session", _fail)

    runner = CliRunner()
    result = runner.invoke(cli, ["create", str(vault_root), participant, "ProjectX", "docs"])
    assert result.exit_code == 0, result.output

    checkout = tmp_path / "checkout"
    result = runner.invoke(
        cli,
        ["checkout", str(vault_root), participant, "ProjectX", "docs", str(checkout)],
    )
    assert result.exit_code == 0, result.output

    assert (
        vault_root
        / "participants"
        / participant
        / "teams"
        / team_id
        / "niches"
        / "docs"
        / "git"
    ).is_dir()
    assert not (
        vault_root
        / "participants"
        / participant
        / "teams"
        / "ProjectX"
    ).exists()
    context = vault.VaultMaterializationContext(participant, team_id, "ProjectX")
    assert vault.get_checkout(str(vault_root), participant, context, "docs") == str(checkout)


def test_cli_local_command_without_materialization_fails(monkeypatch, tmp_path):
    """Local commands error clearly when the team hasn't been logged into."""
    config_file = tmp_path / "vault.toml"
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(config_file))
    participant = "aa" * 16
    vault_root = tmp_path / "vault"
    vault.init_vault(str(vault_root), participant)

    runner = CliRunner()
    result = runner.invoke(cli, ["create", str(vault_root), participant, "ProjectX", "docs"])
    assert result.exit_code == 1
    assert "ProjectX" in result.output
    assert "login" in result.output.lower()


def test_hub_push_pull_refreshes_checkout(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_member_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_vault_root = str(root / "vault-alice")
    bob_vault_root = str(root / "vault-bob")
    vault.init_vault(alice_vault_root, env["alice_hex"])
    vault.init_vault(bob_vault_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    alice_login = sync.login_team(alice_vault_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = vault.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    bob_checkout = root / "bob-checkout"

    vault.create_niche(alice_vault_root, env["alice_hex"], alice_context, "docs")
    vault.add_checkout(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "notes.txt").write_text("v1\n")
    vault.publish(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="init")

    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    raw_latest_link = _read_s3_object(
        env["alice_minio"]["endpoint"],
        env["alice_minio"]["access_key"],
        env["alice_minio"]["secret_key"],
        env["team_bucket"],
        "niches/docs/latest-link.yaml",
    )
    assert b"notes.txt" not in raw_latest_link
    assert b"v1\n" not in raw_latest_link

    # Bob joins: fetch → attach checkout → merge (3-step join flow)
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    bob_login = sync.login_team(bob_vault_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = vault.materialization_context_from_session_info(bob_login.session_info)
    sync.fetch_via_hub(
        bob_vault_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_member_id_hex"],
        _http_client=http,
    )
    vault.add_checkout(bob_vault_root, env["bob_hex"], bob_context, "docs", str(bob_checkout))
    sync.merge_via_hub(
        bob_vault_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_member_id_hex"],
        _http_client=http,
    )
    assert (bob_checkout / "notes.txt").read_text() == "v1\n"

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    (alice_checkout / "notes.txt").write_text("v2\n")
    vault.publish(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="update")
    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    # Subsequent pull: Bob already has a clean checkout, pull_via_hub works directly
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    sync.pull_via_hub(
        bob_vault_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_member_id_hex"],
        _http_client=http,
    )
    assert (bob_checkout / "notes.txt").read_text() == "v2\n"


def test_hub_pull_conflict_reports_paths(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_member_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_vault_root = str(root / "vault-alice")
    bob_vault_root = str(root / "vault-bob")
    vault.init_vault(alice_vault_root, env["alice_hex"])
    vault.init_vault(bob_vault_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    alice_login = sync.login_team(alice_vault_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = vault.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    bob_checkout = root / "bob-checkout"

    vault.create_niche(alice_vault_root, env["alice_hex"], alice_context, "docs")
    vault.add_checkout(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "shared.txt").write_text("base\n")
    vault.publish(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="base")

    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    # Bob joins: fetch → attach checkout → merge (3-step join flow)
    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    bob_login = sync.login_team(bob_vault_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = vault.materialization_context_from_session_info(bob_login.session_info)
    sync.fetch_via_hub(
        bob_vault_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_member_id_hex"],
        _http_client=http,
    )
    vault.add_checkout(bob_vault_root, env["bob_hex"], bob_context, "docs", str(bob_checkout))
    sync.merge_via_hub(
        bob_vault_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_member_id_hex"],
        _http_client=http,
    )

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "alice-vault.toml"))
    (alice_checkout / "shared.txt").write_text("alice change\n")
    vault.publish(alice_vault_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="alice")
    sync.push_via_hub(alice_vault_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    (bob_checkout / "shared.txt").write_text("bob change\n")
    vault.publish(bob_vault_root, env["bob_hex"], bob_context, "docs", str(bob_checkout), message="bob")

    monkeypatch.setenv("SMALL_SEA_VAULT_CONFIG", str(root / "bob-vault.toml"))
    with pytest.raises(sync.PullConflictError) as exc_info:
        sync.pull_via_hub(
            bob_vault_root,
            env["bob_hex"],
            "ProjectX",
            "docs",
            env["alice_member_id_hex"],
            _http_client=http,
        )

    assert exc_info.value.scope == "niche"
    assert "shared.txt" in exc_info.value.paths
