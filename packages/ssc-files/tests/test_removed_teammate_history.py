"""A teammate's commits made before their removal stay valid after it."""

import os
import shutil
import sqlite3

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_hub_sync import _open_session, _push_team_repo_via_hub, _setup_two_teammate_team

from cod_sync.git import signing_git_env
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager, _CORE_APP

import small_sea_manager.provisioning as Provisioning
from ssc_files import files, sync


def _hub_signer(team_name, hub_port=11437, *, _http_client=None, tmp_dir):
    """Sign with the session's real workhorse key and verify through the Hub's /session/verify.

    The Hub signing program is not installed in tests, so the key is exported to a file
    for ssh-keygen; the signature is the one the Hub would produce.
    """
    session = sync.get_team_session(team_name, hub_port, _http_client=_http_client)
    info = session.session_info()
    seed = app.state.backend.signing_key(session.token)
    key_path = tmp_dir / f"key-{session.token[:8]}"
    key_path.write_bytes(Ed25519PrivateKey.from_private_bytes(seed).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()))
    key_path.chmod(0o600)
    env = signing_git_env(
        os.environ, program=shutil.which("ssh-keygen"),
        public_key=session.signing_public_key(), signing_key=str(key_path),
        extra_env={"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return files.CommitSigner(info["team_id"], info["berth_id"], session.authority_view(),
                              env, session.verify_history)


def test_files_history_survives_teammate_removal(playground_dir, minio_server_gen, monkeypatch, tmp_path):
    env = _setup_two_teammate_team(playground_dir, minio_server_gen)
    monkeypatch.setattr(
        sync, "commit_signer",
        lambda team, hub_port=11437, *, _http_client=None: _hub_signer(
            team, hub_port, _http_client=_http_client, tmp_dir=tmp_path))
    root, http = env["root"], env["http"]
    alice_files_root = str(tmp_path / "files-alice")
    bob_files_root = str(tmp_path / "files-bob")
    files.init_files(alice_files_root, env["alice_hex"])
    files.init_files(bob_files_root, env["bob_hex"])

    # Alice needs Bob's sender key to decrypt what Bob uploads,
    # so hand it over before Bob uploads anything.
    distribution = Provisioning.redistribute_sender_key(root, env["bob_hex"], "ProjectX")
    assert len(distribution["artifacts"]) == 1, distribution
    Provisioning.receive_sender_key_distribution(
        root, env["alice_hex"], "ProjectX", distribution["artifacts"][0]["distribution_payload"])
    # Bob signs and publishes a Files commit.
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "bob-files.toml"))
    bob_login = sync.login_team(bob_files_root, "ProjectX", env["bob_hex"], _http_client=http, pin_reader=lambda _: "")
    bob_context = files.materialization_context_from_session_info(bob_login.session_info)
    bob_signer = sync.commit_signer("ProjectX", _http_client=http)
    bob_checkout = tmp_path / "bob-checkout"
    files.create_niche(bob_files_root, env["bob_hex"], bob_context, "docs", signer=bob_signer)
    files.add_checkout(bob_files_root, env["bob_hex"], bob_context, "docs", str(bob_checkout))
    (bob_checkout / "notes.txt").write_text("from bob\n")
    files.publish(bob_files_root, env["bob_hex"], bob_context, "docs", str(bob_checkout), message="bob", signer=bob_signer)
    sync.push_via_hub(bob_files_root, env["bob_hex"], "ProjectX", "docs", _http_client=http)

    # Bob publishes his Core so Alice can fetch his Files storage.
    bob_manager = TeamManager(root, env["bob_hex"], _http_client=http)
    bob_core_token = _open_session(http, "Bob", "ProjectX", app_name=_CORE_APP)
    bob_team_sync = root / "Participants" / env["bob_hex"] / "ProjectX" / "Sync"
    _push_team_repo_via_hub(http, bob_core_token, bob_team_sync, bob_manager, "ProjectX")
    alice_manager = TeamManager(root, env["alice_hex"], _http_client=http)
    alice_manager.fetch_teammate_core("ProjectX", env["bob_teammate_id_hex"])
    alice_manager.integrate_core_sources("ProjectX")
    # Core integration does not import peers' storage announcements yet (provisioning.py,
    # "deferred, #138"), so copy Bob's signed rows into Alice's team database by hand.
    with sqlite3.connect(str(Provisioning._team_db_path(root, env["bob_hex"], "ProjectX"))) as bob_db, \
            sqlite3.connect(str(Provisioning._team_db_path(root, env["alice_hex"], "ProjectX"))) as alice_db:
        rows = bob_db.execute(
            "SELECT * FROM teammate_berth_storage_announcement WHERE teammate_id = ?",
            (bytes.fromhex(env["bob_teammate_id_hex"]),)).fetchall()
        alice_db.executemany(
            "INSERT OR IGNORE INTO teammate_berth_storage_announcement VALUES (?,?,?,?,?,?,?,?,?)", rows)
    report = alice_manager.reconcile_team_route("ProjectX", app_name=sync.HUB_APP_NAME)
    assert report["route"] == "ready", report

    # Alice fetches Bob's history while Bob is still a teammate.
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(root / "alice-files.toml"))
    alice_login = sync.login_team(alice_files_root, "ProjectX", env["alice_hex"], _http_client=http, pin_reader=lambda _: "")
    alice_context = files.materialization_context_from_session_info(alice_login.session_info)
    alice_checkout = tmp_path / "alice-checkout"
    sync.fetch_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", env["bob_teammate_id_hex"], _http_client=http)
    files.add_checkout(alice_files_root, env["alice_hex"], alice_context, "docs", str(alice_checkout))

    # Alice removes Bob; the Hub reads Alice's Core database, so this takes effect locally at once.
    Provisioning.remove_teammate(root, env["alice_hex"], "ProjectX", env["bob_teammate_id_hex"])

    # Bob's earlier commit is still in the fetched history, and it stays valid.
    sync.merge_via_hub(alice_files_root, env["alice_hex"], "ProjectX", "docs", env["bob_teammate_id_hex"], _http_client=http)
    assert (alice_checkout / "notes.txt").read_text() == "from bob\n"
