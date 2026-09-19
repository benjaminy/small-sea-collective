"""Two independent Files homes exchanging one file through separate Hubs."""

import json
import os
import pathlib
import subprocess
import sys
import time

import httpx
import small_sea_manager.provisioning as provisioning
from small_sea_manager.manager import TeamManager, _CORE_APP
from ssc_files import files, sync
from test_support import (
    accept_and_export,
    acceptance_record_from_courier,
    minio_server_gen,
    reserve_ports,
)


TEAM = "ProjectX"


def _start_hub(root, port):
    env = os.environ.copy()
    env.update(
        SMALL_SEA_ROOT_DIR=str(root),
        SMALL_SEA_PORT=str(port),
        SMALL_SEA_AUTO_APPROVE_SESSIONS="true",
        SMALL_SEA_WATCHER_INTERVAL="3600",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "small_sea_hub.server:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if client.get("/").status_code == 200:
                return process, client
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            raise RuntimeError(f"Hub exited with {process.returncode}")
        time.sleep(0.05)
    process.terminate()
    raise RuntimeError("Hub did not start")


def _stop(process, client):
    client.close()
    process.terminate()
    process.wait(timeout=5)


def _session(client, participant, team, app_name=sync.HUB_APP_NAME, mode="encrypted"):
    response = client.post("/sessions/request", json={
        "participant": participant,
        "app": app_name,
        "team": team,
        "client": "files-two-homes",
        "mode": mode,
    })
    response.raise_for_status()
    return response.json()["token"]


def _berth(client, token):
    response = client.get("/session/info", headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    return response.json()["berth_id"]


def _setup_cloud(root, participant, minio):
    return provisioning.add_cloud_storage(
        root,
        participant,
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )


def _push_core(client, root, token, participant):
    from cod_sync.protocol import CodSync
    from cod_sync.repo import Repo
    from cod_sync.store import SmallSeaStore

    response = client.post("/cloud/setup", headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    repo_path = pathlib.Path(root) / "Participants" / participant / TEAM / "Sync"
    CodSync(
        Repo(repo_path / ".git", repo_path),
        SmallSeaStore(token, base_url=str(client.base_url), client=client),
    ).publish()


def test_two_independent_homes_survive_bob_restart(tmp_path, minio_server_gen, monkeypatch):
    alice_root = tmp_path / "alice-home"
    bob_root = tmp_path / "bob-home"
    alice_minio = minio_server_gen()
    bob_minio = minio_server_gen()
    alice_port, bob_port = reserve_ports([None, None])

    alice = provisioning.create_new_participant(alice_root, "Alice")
    bob = provisioning.create_new_participant(bob_root, "Bob")
    for root, participant in ((alice_root, alice), (bob_root, bob)):
        provisioning.register_app_for_participant(root, participant, sync.HUB_APP_NAME)

    alice_process, alice_http = _start_hub(alice_root, alice_port)
    try:
        bob_process, bob_http = _start_hub(bob_root, bob_port)
    except Exception:
        _stop(alice_process, alice_http)
        raise
    first_bob_pid = bob_process.pid
    try:
        _session(alice_http, "Alice", "NoteToSelf", mode="passthrough")
        alice_cloud = _setup_cloud(alice_root, alice, alice_minio)
        _session(bob_http, "Bob", "NoteToSelf", mode="passthrough")
        bob_cloud = _setup_cloud(bob_root, bob, bob_minio)

        team = provisioning.create_team(alice_root, alice, TEAM)
        provisioning.activate_app_for_team(alice_root, alice, TEAM, sync.HUB_APP_NAME)
        alice_token = _session(alice_http, "Alice", TEAM)
        alice_berth = _berth(alice_http, alice_token)
        provisioning.add_berth_cloud_allocation_by_berth_id(
            alice_root, alice, alice_berth, alice_cloud, location=f"ss-{alice_berth[:16]}"
        )
        alice_http.post("/cloud/setup", headers={"Authorization": f"Bearer {alice_token}"}).raise_for_status()
        alice_manager = TeamManager(alice_root, alice, _http_client=alice_http)
        alice_manager.publish_teammate_berth_storage_announcement(
            TEAM,
            alice_berth,
            provisioning.get_berth_cloud_allocation_for_berth(alice_root, alice, alice_berth),
        )

        alice_core = _session(alice_http, "Alice", TEAM, app_name=_CORE_APP)
        _push_core(alice_http, alice_root, alice_core, alice)
        invitation = provisioning.create_invitation(
            alice_root, alice, TEAM,
            {"protocol": "s3", "url": alice_minio["endpoint"]},
            invitee_label="Bob",
        )
        _push_core(alice_http, alice_root, alice_core, alice)

        manager = TeamManager(bob_root, bob, _http_client=bob_http)
        acceptance = accept_and_export(manager, invitation)
        bob_teammate = acceptance_record_from_courier(acceptance)["author_teammate_id"]
        bob_token = _session(bob_http, "Bob", TEAM)
        bob_berth = _berth(bob_http, bob_token)
        provisioning.add_berth_cloud_allocation_by_berth_id(
            bob_root, bob, bob_berth, bob_cloud, location=f"ss-{bob_berth[:16]}"
        )
        bob_http.post("/cloud/setup", headers={"Authorization": f"Bearer {bob_token}"}).raise_for_status()
        provisioning.complete_invitation_acceptance(alice_root, alice, TEAM, acceptance)

        alice_files = tmp_path / "alice-files"
        bob_files = tmp_path / "bob-files"
        alice_checkout = tmp_path / "alice-checkout"
        bob_checkout = tmp_path / "bob-checkout"
        files.init_files(alice_files, alice)
        files.init_files(bob_files, bob)

        monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(tmp_path / "alice-files.toml"))
        alice_login = sync.login_team(alice_files, TEAM, alice, _http_client=alice_http, pin_reader=lambda _: "")
        alice_context = files.materialization_context_from_session_info(alice_login.session_info)
        files.create_niche(alice_files, alice, alice_context, "docs")
        files.add_checkout(alice_files, alice, alice_context, "docs", alice_checkout)
        retained = "two homes, one retained file\n"
        (alice_checkout / "notes.txt").write_text(retained)
        files.publish(alice_files, alice, alice_context, "docs", alice_checkout, message="publish notes")
        sync.push_via_hub(alice_files, alice, TEAM, "docs", _http_client=alice_http)

        monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(tmp_path / "bob-files.toml"))
        bob_login = sync.login_team(bob_files, TEAM, bob, _http_client=bob_http, pin_reader=lambda _: "")
        bob_context = files.materialization_context_from_session_info(bob_login.session_info)
        sync.fetch_via_hub(bob_files, bob, TEAM, "docs", team["teammate_id_hex"], _http_client=bob_http)
        files.add_checkout(bob_files, bob, bob_context, "docs", bob_checkout)
        sync.merge_via_hub(bob_files, bob, TEAM, "docs", team["teammate_id_hex"], _http_client=bob_http)
        assert (bob_checkout / "notes.txt").read_text() == retained

        _stop(bob_process, bob_http)
        bob_process, bob_http = _start_hub(bob_root, bob_port)
        assert bob_process.pid != first_bob_pid

        updated = "two homes, usable after restart\n"
        monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(tmp_path / "alice-files.toml"))
        (alice_checkout / "notes.txt").write_text(updated)
        files.publish(alice_files, alice, alice_context, "docs", alice_checkout, message="post-restart update")
        sync.push_via_hub(alice_files, alice, TEAM, "docs", _http_client=alice_http)
        request = {
            "team": TEAM,
            "participant": bob,
            "files_root": str(bob_files),
            "checkout": str(bob_checkout),
            "path": "notes.txt",
            "niche": "docs",
            "from_teammate_id": team["teammate_id_hex"],
            "hub_port": bob_port,
        }
        fresh_env = os.environ.copy()
        fresh_env["SMALL_SEA_FILES_CONFIG"] = str(tmp_path / "bob-files.toml")
        probe = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).with_name("bob_after_restart.py")), json.dumps(request)],
            check=True,
            capture_output=True,
            text=True,
            env=fresh_env,
        )
        report = json.loads(probe.stdout)
        assert report["pid"] != os.getpid()
        assert report["content"] == updated
        assert report["session_team_name"] == TEAM
        assert report["session_participant_hex"] == bob
        assert report["session_app_name"] == sync.HUB_APP_NAME
        assert report["session_berth_id"] == bob_berth
        assert report["context_team_id"] == bob_context.team_id
        print(json.dumps({
            "alice_hub_pid": alice_process.pid,
            "bob_hub_pid_before": first_bob_pid,
            "bob_hub_pid_after": bob_process.pid,
            "alice_home": str(alice_root),
            "bob_home": str(bob_root),
            "retained_path": str(bob_checkout / "notes.txt"),
            "fresh_process": report,
            "bob_teammate_id": bob_teammate,
        }, sort_keys=True))
    finally:
        _stop(alice_process, alice_http)
        _stop(bob_process, bob_http)
