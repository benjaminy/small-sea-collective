import pathlib

import boto3
import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from botocore.config import Config as BotoConfig
from cod_sync.protocol import (
    PublicationIntegrationRequiredError,
    PublicationOutcomeUnresolvedError,
    PublicationRetryableError,
)
from click.testing import CliRunner
from fastapi.testclient import TestClient
from ssc_files import sync, files
from ssc_files.cli import cli
from small_sea_client.client import SmallSeaClient, SmallSeaSession
from small_sea_hub.adapters.s3 import SmallSeaS3Adapter
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager, _CORE_APP
from test_support import (
    accept_and_export,
    acceptance_record_from_courier,
    publish_storage_announcement_for_session,
)


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
    publish_storage_announcement_for_session(app.state.backend, session_hex)

    from cod_sync.protocol import CodSync
    from cod_sync.repo import Repo
    from cod_sync.store import SmallSeaStore
    remote = SmallSeaStore(session_hex, base_url="http://testserver", client=http)
    repo_path = pathlib.Path(repo_dir)
    cs = CodSync(Repo(repo_path / ".git", repo_path), remote)
    cs.publish()


def _session_berth_info(http, session_hex):
    return http.get(
        "/session/info",
        headers={"Authorization": f"Bearer {session_hex}"},
    ).json()


def _setup_two_teammate_team(playground_dir, minio_server_gen):
    alice_minio = minio_server_gen()
    bob_minio = minio_server_gen()
    root = pathlib.Path(playground_dir)

    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    alice_hex = Provisioning.create_new_participant(root, "Alice")
    bob_hex = Provisioning.create_new_participant(root, "Bob")
    Provisioning.register_app_for_participant(root, alice_hex, sync.HUB_APP_NAME)
    Provisioning.register_app_for_participant(root, bob_hex, sync.HUB_APP_NAME)

    alice_nts = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    alice_cloud_id = Provisioning.add_cloud_storage(
        root,
        alice_hex,
        protocol="s3",
        url=alice_minio["endpoint"],
        access_key=alice_minio["access_key"],
        secret_key=alice_minio["secret_key"],
    )
    bob_nts = _open_session(http, "Bob", "NoteToSelf", mode="passthrough")
    bob_cloud_id = Provisioning.add_cloud_storage(
        root,
        bob_hex,
        protocol="s3",
        url=bob_minio["endpoint"],
        access_key=bob_minio["access_key"],
        secret_key=bob_minio["secret_key"],
    )

    team_result = Provisioning.create_team(root, alice_hex, "ProjectX")
    Provisioning.activate_app_for_team(root, alice_hex, "ProjectX", sync.HUB_APP_NAME)
    alice_teammate_id_hex = team_result["teammate_id_hex"]

    alice_team_token = _open_session(http, "Alice", "ProjectX")
    alice_files_berth = _session_berth_info(http, alice_team_token)["berth_id"]
    # Keep this fixture's public bucket stable so older peer-sync assertions
    # can inspect exactly where Alice's app berth writes.
    team_bucket = f"ss-{alice_files_berth[:16]}"
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root,
        alice_hex,
        alice_files_berth,
        alice_cloud_id,
        location=team_bucket,
    )
    alice_team_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    resp = http.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {alice_team_token}"},
    )
    assert resp.status_code == 200, resp.text
    publish_storage_announcement_for_session(backend, alice_team_token)

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
    acceptance_b64 = accept_and_export(bob_manager, token_b64)
    acceptance = acceptance_record_from_courier(acceptance_b64)
    bob_teammate_id_hex = acceptance["author_teammate_id"]
    bob_team_token = _open_session(http, "Bob", "ProjectX")
    bob_files_berth = _session_berth_info(http, bob_team_token)["berth_id"]
    # Match Alice's stable fixture shape for Bob's own app berth.
    Provisioning.add_berth_cloud_allocation_by_berth_id(
        root,
        bob_hex,
        bob_files_berth,
        bob_cloud_id,
        location=f"ss-{bob_files_berth[:16]}",
    )
    resp = http.post(
        "/cloud/setup",
        headers={"Authorization": f"Bearer {bob_team_token}"},
    )
    assert resp.status_code == 200, resp.text
    publish_storage_announcement_for_session(backend, bob_team_token)
    Provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance_b64)

    return {
        "root": root,
        "http": http,
        "alice_minio": alice_minio,
        "alice_hex": alice_hex,
        "bob_hex": bob_hex,
        "alice_teammate_id_hex": alice_teammate_id_hex,
        "bob_teammate_id_hex": bob_teammate_id_hex,
        "team_bucket": team_bucket,
    }


def test_signal_watermark_roundtrip(tmp_path, monkeypatch):
    root = str(tmp_path / "files")
    participant = "aa" * 16
    files.init_files(root, participant)
    team_a = files.FilesMaterializationContext(participant, "11" * 16, "TeamA")
    team_b = files.FilesMaterializationContext(participant, "22" * 16, "TeamB")
    team_c = files.FilesMaterializationContext(participant, "33" * 16, "TeamC")

    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 0

    sync.set_signal_watermark(root, participant, team_a, "aa" * 16, 5)
    sync.set_signal_watermark(root, participant, team_a, "bb" * 16, 2)
    sync.set_signal_watermark(root, participant, team_b, "aa" * 16, 9)

    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 5
    assert sync.get_signal_watermark(root, participant, team_a, "bb" * 16) == 2
    assert sync.get_signal_watermark(root, participant, team_b, "aa" * 16) == 9
    # Unrelated team/teammate still 0
    assert sync.get_signal_watermark(root, participant, team_c, "cc" * 16) == 0

    sync.clear_signal_watermark(root, participant, team_a, "aa" * 16)
    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 0
    # Other entries untouched
    assert sync.get_signal_watermark(root, participant, team_a, "bb" * 16) == 2


def test_signal_watermark_persists_alongside_session_token(tmp_path, monkeypatch):
    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))

    sync.save_config(
        {
            "files_root": str(tmp_path / "v"),
            "team_sessions": {"TeamA": {"session_token": "tok"}},
        }
    )
    root = str(tmp_path / "v")
    participant = "aa" * 16
    team_a = files.FilesMaterializationContext(participant, "11" * 16, "TeamA")
    files.init_files(root, participant)
    sync.set_signal_watermark(root, participant, team_a, "aa" * 16, 7)

    loaded = sync.load_config()
    assert loaded["team_sessions"]["TeamA"]["session_token"] == "tok"
    assert "peer_signal_watermarks" not in loaded
    assert sync.get_signal_watermark(root, participant, team_a, "aa" * 16) == 7


def test_peer_update_status_has_unfetched_hint(tmp_path, monkeypatch, playground_dir):
    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))

    root = playground_dir
    participant = "bb" * 16
    teammate_id = "cc" * 16
    team = files.FilesMaterializationContext(participant, "44" * 16, "HintTeam")
    niche = "files"

    files.init_files(root, participant)
    files.materialize_team(root, team)
    files.create_niche(root, participant, team, niche)

    # No watermark set → current 3 > watermark 0 → hint True
    status = sync.peer_update_status(root, participant, team, niche, teammate_id,
                                     current_signal_count=3)
    assert status.has_unfetched_hint is True
    assert status.current_signal_count == 3
    assert status.last_seen_signal_count == 0

    # Set watermark to match → hint False
    sync.set_signal_watermark(root, participant, team, teammate_id, 3)
    status = sync.peer_update_status(root, participant, team, niche, teammate_id,
                                     current_signal_count=3)
    assert status.has_unfetched_hint is False

    # current 0, watermark 0 → hint False (never pushed)
    status = sync.peer_update_status(root, participant, team, niche, "dd" * 16,
                                     current_signal_count=0)
    assert status.has_unfetched_hint is False


def test_fetch_via_hub_advances_watermark(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    bob_files_root = str(root / "files-bob")
    files.init_files(alice_files_root, env["alice_hex"])
    files.init_files(bob_files_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    alice_login = sync.login_team(alice_files_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = files.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    files.create_niche(alice_files_root, env["alice_hex"], alice_context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "file.txt").write_text("hello\n")
    files.publish(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="init")

    sync.push_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    bob_token = sync.login_team(bob_files_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = files.materialization_context_from_session_info(bob_token.session_info)

    # Seed peer_counts so /session/peers reports Alice's signal as 5.
    # The background watcher is not running in TestClient, so we set it directly.
    bob_session = SmallSeaSession(SmallSeaClient(port=11437, _http_client=http), bob_token.session_token)
    bob_berth_id_hex = bob_session.session_info()["berth_id"]
    if not hasattr(app.state, "peer_counts"):
        app.state.peer_counts = {}
    app.state.peer_counts[(bob_berth_id_hex, env["alice_teammate_id_hex"])] = 5

    assert sync.get_signal_watermark(
        bob_files_root, env["bob_hex"], bob_context, env["alice_teammate_id_hex"]
    ) == 0

    sync.fetch_via_hub(
        bob_files_root, env["bob_hex"], "ProjectX", "docs",
        env["alice_teammate_id_hex"], _http_client=http,
    )

    # Watermark should equal the observed signal_count (5)
    assert sync.get_signal_watermark(
        bob_files_root, env["bob_hex"], bob_context, env["alice_teammate_id_hex"]
    ) == 5


def test_fetch_via_hub_does_not_touch_other_peers_watermark(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    bob_files_root = str(root / "files-bob")
    files.init_files(alice_files_root, env["alice_hex"])
    files.init_files(bob_files_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    alice_login = sync.login_team(alice_files_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = files.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    files.create_niche(alice_files_root, env["alice_hex"], alice_context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "file.txt").write_text("hello\n")
    files.publish(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="init")

    sync.push_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    bob_login = sync.login_team(bob_files_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = files.materialization_context_from_session_info(bob_login.session_info)

    # Plant a watermark for a different fake peer
    other_teammate = "ff" * 16
    sync.set_signal_watermark(bob_files_root, env["bob_hex"], bob_context, other_teammate, 99)

    sync.fetch_via_hub(
        bob_files_root, env["bob_hex"], "ProjectX", "docs",
        env["alice_teammate_id_hex"], _http_client=http,
    )

    # Other peer's watermark is untouched
    assert sync.get_signal_watermark(bob_files_root, env["bob_hex"], bob_context, other_teammate) == 99


def test_sync_config_roundtrip_and_remote_prefixes(tmp_path, monkeypatch):
    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))

    sync.save_config(
        {
            "files_root": "/tmp/files",
            "participant_hex": "aa" * 16,
            "hub_port": 12345,
            "team_sessions": {"Project X": {"session_token": "tok-123"}},
        }
    )

    loaded = sync.load_config()
    assert loaded["files_root"] == "/tmp/files"
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
    config_file = root / "alice-files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))

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
    alice_files_root = str(root / "files-alice")
    try:
        result = sync.login_team(
            alice_files_root,
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
    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))
    sync.save_config(
        {
            "files_root": "/tmp/example-files",
            "participant_hex": "aa" * 16,
            "hub_port": 23456,
        }
    )

    captured = {}

    def _fake_push(files_root, participant_hex, team_name, niche_name, *, hub_port, _http_client=None):
        captured.update(
            {
                "files_root": files_root,
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
        "files_root": "/tmp/example-files",
        "participant_hex": "aa" * 16,
        "team_name": "ProjectX",
        "niche_name": "docs",
        "hub_port": 23456,
    }


def test_cli_local_commands_resolve_offline_from_metadata(monkeypatch, tmp_path):
    """Local CLI commands resolve friendly team_name to team_id from metadata.json.

    No Hub call is required: once a team has been materialized (via login or
    test-direct files.materialize_team), subsequent local operations read the
    team_id from metadata.json offline.
    """
    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))
    participant = "aa" * 16
    team_id = "11" * 16
    files_root = tmp_path / "files"
    files.init_files(str(files_root), participant)
    files.materialize_team(
        str(files_root),
        files.FilesMaterializationContext(participant, team_id, "ProjectX"),
    )

    def _fail(*_a, **_kw):
        raise AssertionError("local CLI commands must not contact the Hub")

    monkeypatch.setattr(sync, "get_team_session", _fail)

    runner = CliRunner()
    result = runner.invoke(cli, ["create", str(files_root), participant, "ProjectX", "docs"])
    assert result.exit_code == 0, result.output

    checkout = tmp_path / "checkout"
    result = runner.invoke(
        cli,
        ["checkout", str(files_root), participant, "ProjectX", "docs", str(checkout)],
    )
    assert result.exit_code == 0, result.output

    assert (
        files_root
        / "participants"
        / participant
        / "teams"
        / team_id
        / "niches"
        / "docs"
        / "git"
    ).is_dir()
    assert not (
        files_root
        / "participants"
        / participant
        / "teams"
        / "ProjectX"
    ).exists()
    context = files.FilesMaterializationContext(participant, team_id, "ProjectX")
    assert files.get_checkout(str(files_root), participant, context, "docs") == str(checkout)


def test_cli_local_command_without_materialization_fails(monkeypatch, tmp_path):
    """Local commands error clearly when the team hasn't been logged into."""
    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))
    participant = "aa" * 16
    files_root = tmp_path / "files"
    files.init_files(str(files_root), participant)

    runner = CliRunner()
    result = runner.invoke(cli, ["create", str(files_root), participant, "ProjectX", "docs"])
    assert result.exit_code == 1
    assert "ProjectX" in result.output
    assert "login" in result.output.lower()


def test_hub_push_pull_refreshes_checkout(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    bob_files_root = str(root / "files-bob")
    files.init_files(alice_files_root, env["alice_hex"])
    files.init_files(bob_files_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    alice_login = sync.login_team(alice_files_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = files.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    bob_checkout = root / "bob-checkout"

    files.create_niche(alice_files_root, env["alice_hex"], alice_context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "notes.txt").write_text("v1\n")
    files.publish(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="init")

    sync.push_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

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
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    bob_login = sync.login_team(bob_files_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = files.materialization_context_from_session_info(bob_login.session_info)
    sync.fetch_via_hub(
        bob_files_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_teammate_id_hex"],
        _http_client=http,
    )
    files.add_checkout(bob_files_root, env["bob_hex"], bob_context, "docs", str(bob_checkout))
    sync.merge_via_hub(
        bob_files_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_teammate_id_hex"],
        _http_client=http,
    )
    assert (bob_checkout / "notes.txt").read_text() == "v1\n"

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    (alice_checkout / "notes.txt").write_text("v2\n")
    files.publish(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="update")
    sync.push_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    # Subsequent pull: Bob already has a clean checkout, pull_via_hub works directly
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    sync.pull_via_hub(
        bob_files_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_teammate_id_hex"],
        _http_client=http,
    )
    assert (bob_checkout / "notes.txt").read_text() == "v2\n"


def test_hub_pull_conflict_reports_paths(playground_dir, minio_server_gen, monkeypatch):
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    bob_files_root = str(root / "files-bob")
    files.init_files(alice_files_root, env["alice_hex"])
    files.init_files(bob_files_root, env["bob_hex"])

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    alice_login = sync.login_team(alice_files_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = files.materialization_context_from_session_info(alice_login.session_info)

    alice_checkout = root / "alice-checkout"
    bob_checkout = root / "bob-checkout"

    files.create_niche(alice_files_root, env["alice_hex"], alice_context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))
    (alice_checkout / "shared.txt").write_text("base\n")
    files.publish(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="base")

    sync.push_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    # Bob joins: fetch → attach checkout → merge (3-step join flow)
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    bob_login = sync.login_team(bob_files_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = files.materialization_context_from_session_info(bob_login.session_info)
    sync.fetch_via_hub(
        bob_files_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_teammate_id_hex"],
        _http_client=http,
    )
    files.add_checkout(bob_files_root, env["bob_hex"], bob_context, "docs", str(bob_checkout))
    sync.merge_via_hub(
        bob_files_root,
        env["bob_hex"],
        "ProjectX",
        "docs",
        env["alice_teammate_id_hex"],
        _http_client=http,
    )

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    (alice_checkout / "shared.txt").write_text("alice change\n")
    files.publish(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout), message="alice")
    sync.push_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http)

    (bob_checkout / "shared.txt").write_text("bob change\n")
    files.publish(bob_files_root, env["bob_hex"], bob_context, "docs", str(bob_checkout), message="bob")

    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    with pytest.raises(sync.PullConflictError) as exc_info:
        sync.pull_via_hub(
            bob_files_root,
            env["bob_hex"],
            "ProjectX",
            "docs",
            env["alice_teammate_id_hex"],
            _http_client=http,
        )

    assert exc_info.value.scope == "niche"
    assert "shared.txt" in exc_info.value.paths


def test_push_with_no_new_commits_reports_nothing_to_push(
    playground_dir, minio_server_gen, monkeypatch
):
    """The typed no-op has to surface as the app's existing vocabulary.

    Before this branch the signal was a string match on git's "Refusing to
    create empty bundle", and nothing tested that it still reached the caller.
    """
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    files.init_files(alice_files_root, env["alice_hex"])
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    login = sync.login_team(
        alice_files_root, "ProjectX", env["alice_hex"], _http_client=http,
        pin_reader=lambda _: "",
    )
    context = files.materialization_context_from_session_info(login.session_info)

    checkout = root / "alice-checkout"
    files.create_niche(alice_files_root, env["alice_hex"], context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], context, "docs", str(checkout))
    (checkout / "notes.txt").write_text("v1\n")
    files.publish(
        alice_files_root, env["alice_hex"], context, "docs", str(checkout), message="init"
    )

    sync.push_via_hub(
        alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
    )

    # Second push: the niche is unchanged, and the registry is unchanged too —
    # which is a success, not a failure worth reporting.
    with pytest.raises(sync.NothingToPushError):
        sync.push_via_hub(
            alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
        )


def test_push_preserves_every_typed_publication_state(
    playground_dir, minio_server_gen, monkeypatch
):
    """Each attention state reaches the user surface as a Files sync error.

    A typed Cod Sync error escaping push_via_hub would leave the CLI and the
    web handler with a traceback, since both catch FilesSyncError. The
    translation keeps the typed publication so the surface can report its
    evidence instead of a store message.
    """
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    files.init_files(alice_files_root, env["alice_hex"])
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    login = sync.login_team(
        alice_files_root, "ProjectX", env["alice_hex"], _http_client=http,
        pin_reader=lambda _: "",
    )
    context = files.materialization_context_from_session_info(login.session_info)

    checkout = root / "alice-checkout"
    files.create_niche(alice_files_root, env["alice_hex"], context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], context, "docs", str(checkout))
    (checkout / "notes.txt").write_text("v1\n")
    files.publish(
        alice_files_root, env["alice_hex"], context, "docs", str(checkout), message="init"
    )

    cases = [
        (PublicationRetryableError, sync.PushRetryableError),
        (PublicationIntegrationRequiredError, sync.PushConflictError),
        (PublicationOutcomeUnresolvedError, sync.PushOutcomeUnresolvedError),
    ]
    for publication_error, expected in cases:
        def _fail(*_args, **_kwargs):
            raise publication_error("injected", attempted_head="0" * 40)

        monkeypatch.setattr(files, "push_niche", _fail)
        with pytest.raises(expected) as excinfo:
            sync.push_via_hub(
                alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
            )
        raised = excinfo.value
        assert isinstance(raised, sync.FilesSyncError)
        assert raised.scope == "niche"
        assert isinstance(raised.publication, publication_error)
        assert raised.publication.attempted_head == "0" * 40
        if expected is sync.PushRetryableError:
            assert "can no longer change the cloud head" in str(raised)
            assert "no cloud change was made" not in str(raised)

    unresolved = PublicationOutcomeUnresolvedError(
        "injected registry outcome", attempted_head="0" * 40
    )
    registry_error = sync.PushOutcomeUnresolvedError("registry", unresolved)
    assert "check the registry before pushing again" in str(registry_error)
    assert registry_error.publication is unresolved

    # The conflict names the self-store integration, not a teammate pull.
    def _diverged(*_args, **_kwargs):
        raise PublicationIntegrationRequiredError("injected", attempted_head="0" * 40)

    monkeypatch.setattr(files, "push_niche", _diverged)
    with pytest.raises(sync.PushConflictError) as excinfo:
        sync.push_via_hub(
            alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
        )
    assert "merge --from-self ProjectX docs" in str(excinfo.value)
    assert "teammate" not in str(excinfo.value)


def test_push_repairs_a_registry_left_unpublished_by_an_earlier_failure(
    playground_dir, minio_server_gen, monkeypatch
):
    """An already-present niche must not block the registry publication.

    The niche and the registry are two publications, so a push that moves the
    niche and then fails on the registry leaves the registry outstanding. The
    next push finds the niche already present, and that is exactly the case
    where the registry still needs publishing.
    """
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    files.init_files(alice_files_root, env["alice_hex"])
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    login = sync.login_team(
        alice_files_root, "ProjectX", env["alice_hex"], _http_client=http,
        pin_reader=lambda _: "",
    )
    context = files.materialization_context_from_session_info(login.session_info)

    checkout = root / "alice-checkout"
    files.create_niche(alice_files_root, env["alice_hex"], context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], context, "docs", str(checkout))
    (checkout / "notes.txt").write_text("v1\n")
    files.publish(
        alice_files_root, env["alice_hex"], context, "docs", str(checkout), message="init"
    )

    real_push_registry = files.push_registry

    def _registry_transport_failure(*_args, **_kwargs):
        raise PublicationRetryableError(
            "injected registry transport failure",
            attempted_head="0" * 40,
        )

    monkeypatch.setattr(files, "push_registry", _registry_transport_failure)
    with pytest.raises(sync.PushRetryableError) as excinfo:
        sync.push_via_hub(
            alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
        )
    assert excinfo.value.scope == "registry"

    monkeypatch.setattr(files, "push_registry", real_push_registry)
    # The niche is already present now, and the registry is not.
    sync.push_via_hub(
        alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
    )

    session = sync.get_team_session("ProjectX", _http_client=http)
    repaired = files.push_registry(
        alice_files_root,
        env["alice_hex"],
        context,
        sync.make_registry_remote(session),
    )
    assert repaired.disposition == "already_present"


def test_push_succeeds_when_only_the_registry_is_unchanged(
    playground_dir, minio_server_gen, monkeypatch
):
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root = env["root"]
    http = env["http"]

    alice_files_root = str(root / "files-alice")
    files.init_files(alice_files_root, env["alice_hex"])
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    login = sync.login_team(
        alice_files_root, "ProjectX", env["alice_hex"], _http_client=http,
        pin_reader=lambda _: "",
    )
    context = files.materialization_context_from_session_info(login.session_info)

    checkout = root / "alice-checkout"
    files.create_niche(alice_files_root, env["alice_hex"], context, "docs")
    files.add_checkout(alice_files_root, env["alice_hex"], context, "docs", str(checkout))
    (checkout / "notes.txt").write_text("v1\n")
    files.publish(
        alice_files_root, env["alice_hex"], context, "docs", str(checkout), message="init"
    )
    sync.push_via_hub(
        alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
    )

    # New niche content, same set of niches: the registry no-op must not turn a
    # real push into a failure.
    (checkout / "notes.txt").write_text("v2\n")
    files.publish(
        alice_files_root, env["alice_hex"], context, "docs", str(checkout), message="update"
    )
    sync.push_via_hub(
        alice_files_root, env["alice_hex"], "ProjectX", "docs", _http_client=http
    )


def _record_hub_requests(http):
    """Record (method, path) of every request sent through the TestClient."""
    calls = []
    real_get, real_post = http.get, http.post

    def get(url, *a, **k):
        calls.append(("GET", url))
        return real_get(url, *a, **k)

    def post(url, *a, **k):
        calls.append(("POST", url))
        return real_post(url, *a, **k)

    http.get, http.post = get, post
    return calls


def _bucket_snapshot(minio, bucket):
    s3 = boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )
    objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
    return {o["Key"]: o["ETag"] for o in objects}


def _git_out(git_dir, *args):
    import subprocess

    return subprocess.run(
        ["git", "--git-dir", str(git_dir), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_fetch_self_via_hub_parks_own_head_through_hub_and_minio(
    playground_dir, minio_server_gen, monkeypatch
):
    """Two Files roots for Alice share one in-process Hub and one MinIO store.

    This is one backend with two local Files roots, not independent devices.
    The HTTP transport is an ASGI TestClient, not real TCP. Publications are
    unsigned, so the parked head shows what Alice's store held, not who wrote it.
    """
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root, http = env["root"], env["http"]
    alice, bob = env["alice_hex"], env["bob_hex"]

    # Record every provider write the Hub makes, so the fetch below can be
    # checked for writes that ETags would miss (rewriting identical bytes).
    provider_writes = []
    real_upload = SmallSeaS3Adapter._upload

    def _recording_upload(self, path, *args, **kwargs):
        provider_writes.append(path)
        return real_upload(self, path, *args, **kwargs)

    monkeypatch.setattr(SmallSeaS3Adapter, "_upload", _recording_upload)

    # Bob publishes a different "docs" head to his own store: a control that
    # Alice's own-store fetch must not pick up.
    bob_root = str(root / "files-bob")
    files.init_files(bob_root, bob)
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    bob_ctx = files.materialization_context_from_session_info(
        sync.login_team(bob_root, "ProjectX", bob, _http_client=http, pin_reader=lambda _: "").session_info
    )
    files.create_niche(bob_root, bob, bob_ctx, "docs")
    files.add_checkout(bob_root, bob, bob_ctx, "docs", str(root / "bob-checkout"))
    (root / "bob-checkout" / "bob.txt").write_text("from Bob\n")
    files.publish(bob_root, bob, bob_ctx, "docs", str(root / "bob-checkout"), message="bob")
    sync.push_via_hub(bob_root, bob, "ProjectX", "docs", _http_client=http)
    bob_head = _git_out(files._niche_git_dir(bob_root, bob_ctx, "docs"), "rev-parse", "HEAD")

    # Alice device 1 publishes to her own store.
    a1_root = str(root / "files-alice-1")
    files.init_files(a1_root, alice)
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-1.toml"))
    a1_ctx = files.materialization_context_from_session_info(
        sync.login_team(a1_root, "ProjectX", alice, _http_client=http, pin_reader=lambda _: "").session_info
    )
    files.create_niche(a1_root, alice, a1_ctx, "docs")
    files.add_checkout(a1_root, alice, a1_ctx, "docs", str(root / "a1-checkout"))
    (root / "a1-checkout" / "notes.txt").write_text("from A1\n")
    files.publish(a1_root, alice, a1_ctx, "docs", str(root / "a1-checkout"), message="a1")
    sync.push_via_hub(a1_root, alice, "ProjectX", "docs", _http_client=http)
    a1_niche = _git_out(files._niche_git_dir(a1_root, a1_ctx, "docs"), "rev-parse", "HEAD")
    a1_registry = _git_out(files._registry_git_dir(a1_root, a1_ctx), "rev-parse", "HEAD")
    assert a1_niche != bob_head

    # Alice device 2: its own Files root, config and session token.
    a2_root = str(root / "files-alice-2")
    files.init_files(a2_root, alice)
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-2.toml"))
    a2_ctx = files.materialization_context_from_session_info(
        sync.login_team(a2_root, "ProjectX", alice, _http_client=http, pin_reader=lambda _: "").session_info
    )
    assert a2_ctx.team_id == a1_ctx.team_id

    before = _bucket_snapshot(env["alice_minio"], env["team_bucket"])
    # Pushes above went through the recorder, so it is live.
    assert provider_writes
    writes_before_fetch = len(provider_writes)
    calls = _record_hub_requests(http)
    result = sync.fetch_self_via_hub(a2_root, alice, "ProjectX", "docs", _http_client=http)
    assert provider_writes[writes_before_fetch:] == []

    assert (result.registry_sha, result.niche_sha) == (a1_registry, a1_niche)
    assert calls and all(c == ("GET", "/cloud_file") for c in calls), calls
    assert _bucket_snapshot(env["alice_minio"], env["team_bucket"]) == before

    a2_niche_git = files._niche_git_dir(a2_root, a2_ctx, "docs")
    parked = _git_out(a2_niche_git, "for-each-ref", "--format=%(objectname)", "refs/cod-sync/parked")
    assert parked == a1_niche
    # The niche came only through the fetch; no local branch exists until merge.
    assert _git_out(a2_niche_git, "for-each-ref", "refs/heads") == ""

    a2_checkout = root / "a2-checkout"
    files.add_checkout(a2_root, alice, a2_ctx, "docs", str(a2_checkout))
    assert not (a2_checkout / "notes.txt").exists()
    merged = sync.merge_self(a2_root, alice, "ProjectX", "docs")
    assert merged.niche_shas == [a1_niche]
    assert (a2_checkout / "notes.txt").read_text() == "from A1\n"
    assert not (a2_checkout / "bob.txt").exists()


def test_fetch_self_via_hub_missing_niche_keeps_registry_parked(
    playground_dir, minio_server_gen, monkeypatch
):
    """A niche absent from real MinIO fails after the registry was parked."""
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    root, http, alice = env["root"], env["http"], env["alice_hex"]

    a1_root = str(root / "files-alice-1")
    files.init_files(a1_root, alice)
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-1.toml"))
    a1_ctx = files.materialization_context_from_session_info(
        sync.login_team(a1_root, "ProjectX", alice, _http_client=http, pin_reader=lambda _: "").session_info
    )
    files.create_niche(a1_root, alice, a1_ctx, "docs")
    files.add_checkout(a1_root, alice, a1_ctx, "docs", str(root / "a1-checkout"))
    (root / "a1-checkout" / "notes.txt").write_text("from A1\n")
    files.publish(a1_root, alice, a1_ctx, "docs", str(root / "a1-checkout"), message="a1")
    sync.push_via_hub(a1_root, alice, "ProjectX", "docs", _http_client=http)
    a1_registry = _git_out(files._registry_git_dir(a1_root, a1_ctx), "rev-parse", "HEAD")

    a2_root = str(root / "files-alice-2")
    files.init_files(a2_root, alice)
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-2.toml"))
    a2_ctx = files.materialization_context_from_session_info(
        sync.login_team(a2_root, "ProjectX", alice, _http_client=http, pin_reader=lambda _: "").session_info
    )

    before = _bucket_snapshot(env["alice_minio"], env["team_bucket"])
    with pytest.raises(sync.SelfFetchPartialError) as exc_info:
        sync.fetch_self_via_hub(a2_root, alice, "ProjectX", "never-pushed", _http_client=http)
    assert isinstance(exc_info.value, sync.FilesSyncError)
    assert exc_info.value.registry_sha == a1_registry
    assert _bucket_snapshot(env["alice_minio"], env["team_bucket"]) == before

    registry_git = files._registry_git_dir(a2_root, a2_ctx)
    parked = _git_out(registry_git, "for-each-ref", "--format=%(objectname)", "refs/cod-sync/parked")
    assert parked == a1_registry
    # The failed niche fetch leaves an empty local git dir with no refs.
    niche_git = files._niche_git_dir(a2_root, a2_ctx, "never-pushed")
    assert niche_git.exists()
    assert _git_out(niche_git, "for-each-ref") == ""
