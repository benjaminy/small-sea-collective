"""Dropbox ping-pong round-trip latency test.

Alice and Bob each own a niche in a shared vault. This test:
  1. Pushes and cross-pulls the niche registry so both participants know
     about the niche.
  2. Alice writes a ping file and pushes it to Dropbox via her Hub. Her Hub
     bumps signals.yaml automatically (notify=True in CodSync upload).
  3. Bob polls GET /peer_signal on his Hub until he detects Alice's update,
     then pulls the niche, writes a pong file, and pushes back.
  4. Alice polls and pulls Bob's pong file.
  5. Prints one-way and round-trip latency.

Requires:
  - SMALL_SEA_DROPBOX_WORKSPACE pointing at a workspace built by
    scripts/setup_dropbox_workspace.py (two participants, one shared team).
  - Two available TCP ports (HUB_A_PORT, HUB_B_PORT).

Each Hub gets its own root dir (an isolated copy of its participant's
directory) so they don't share SQLite state.
"""

import os
import pathlib
import shutil
import sqlite3
import subprocess
import time

import pytest
import requests

import cod_sync.protocol as CS
from shared_file_vault.vault import (
    add_checkout,
    create_niche,
    init_vault,
    publish,
    pull_niche,
    pull_registry,
    push_niche,
    push_registry,
)

HUB_A_PORT = 11700
HUB_B_PORT = 11701
NICHE = "ping-pong"
HUB_STARTUP_TIMEOUT = 30   # seconds
SIGNAL_POLL_TIMEOUT = 120  # seconds to wait for peer signal


# ---------------------------------------------------------------------------
# Workspace discovery helpers
# ---------------------------------------------------------------------------


def _discover_participants(workspace: pathlib.Path) -> list[str]:
    """Return sorted list of participant hex dirs under workspace/Participants/."""
    participants_dir = workspace / "Participants"
    if not participants_dir.exists():
        raise ValueError(f"No Participants/ dir in workspace: {workspace}")
    hexes = sorted(d.name for d in participants_dir.iterdir() if d.is_dir())
    if len(hexes) != 2:
        raise ValueError(f"Expected 2 participants, found {len(hexes)}: {hexes}")
    return hexes


def _get_nickname(workspace: pathlib.Path, participant_hex: str) -> str:
    """Read participant nickname from NoteToSelf DB."""
    db = workspace / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT name FROM nickname LIMIT 1").fetchone()
    finally:
        conn.close()
    return row[0] if row else participant_hex


def _get_member_id_hex(workspace: pathlib.Path, participant_hex: str, team_name: str) -> str:
    """Read participant's member_id in team from NoteToSelf DB."""
    db = workspace / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT self_in_team FROM team WHERE name = ?", (team_name,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(
            f"No team '{team_name}' in NoteToSelf DB for participant {participant_hex}"
        )
    return row[0].hex()


def _discover_team_name(workspace: pathlib.Path, participant_hex: str) -> str:
    """Discover the non-NoteToSelf team name from a participant's directory."""
    p_dir = workspace / "Participants" / participant_hex
    team_dirs = [d.name for d in p_dir.iterdir() if d.is_dir() and d.name != "NoteToSelf"]
    if len(team_dirs) != 1:
        raise ValueError(
            f"Expected exactly 1 team (besides NoteToSelf) for {participant_hex}, "
            f"found: {team_dirs}"
        )
    return team_dirs[0]


# ---------------------------------------------------------------------------
# Hub lifecycle helpers
# ---------------------------------------------------------------------------


def _start_hub(root_dir: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["SMALL_SEA_ROOT_DIR"] = root_dir
    env["SMALL_SEA_AUTO_APPROVE_SESSIONS"] = "1"
    cmd = [
        "uv", "run", "fastapi", "dev",
        "packages/small-sea-hub/small_sea_hub/server.py",
        "--port", str(port),
    ]
    return subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_for_hub(endpoint: str, timeout: int = HUB_STARTUP_TIMEOUT) -> None:
    """Poll until Hub responds or raise on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{endpoint}/docs", timeout=2)
            if resp.status_code < 500:
                return
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Hub at {endpoint} did not start within {timeout}s")


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _open_session(endpoint: str, nickname: str, team: str) -> str:
    """Request a session. With auto_approve_sessions=1, returns token directly."""
    resp = requests.post(
        f"{endpoint}/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": team,
            "client": "LatencyTest",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    if "token" in body:
        return body["token"]
    # Fallback: handle PIN flow if auto-approve is somehow disabled
    pending_id = body["pending_id"]
    pin = body["pin"]
    resp2 = requests.post(
        f"{endpoint}/sessions/confirm",
        json={"pending_id": pending_id, "pin": pin},
    )
    resp2.raise_for_status()
    return resp2.json()


# ---------------------------------------------------------------------------
# Signal polling helpers
# ---------------------------------------------------------------------------


def _get_signal_etag(endpoint: str, token: str, member_id_hex: str) -> str | None:
    """Return current etag of peer's signal file, or None if not yet present."""
    resp = requests.get(
        f"{endpoint}/peer_signal",
        params={"member_id": member_id_hex},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()["etag"]


def _poll_for_signal_change(
    endpoint: str, token: str, member_id_hex: str,
    known_etag: str | None, timeout: int = SIGNAL_POLL_TIMEOUT,
) -> None:
    """Block until peer's signal etag differs from known_etag."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        etag = _get_signal_etag(endpoint, token, member_id_hex)
        if etag is not None and etag != known_etag:
            return
        time.sleep(1)
    raise TimeoutError(
        f"Signal for {member_id_hex[:8]}… did not change within {timeout}s"
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dropbox_env(tmp_path_factory):
    workspace_str = os.environ.get("SMALL_SEA_DROPBOX_WORKSPACE")
    if not workspace_str:
        pytest.skip("SMALL_SEA_DROPBOX_WORKSPACE not set — skipping Dropbox latency test")

    workspace = pathlib.Path(workspace_str).expanduser().resolve()
    if not workspace.exists():
        pytest.skip(f"SMALL_SEA_DROPBOX_WORKSPACE path does not exist: {workspace}")

    hexes = _discover_participants(workspace)
    p0_hex, p1_hex = hexes

    team_name = _discover_team_name(workspace, p0_hex)
    p0_nick = _get_nickname(workspace, p0_hex)
    p1_nick = _get_nickname(workspace, p1_hex)
    p0_member_id = _get_member_id_hex(workspace, p0_hex, team_name)
    p1_member_id = _get_member_id_hex(workspace, p1_hex, team_name)

    # Isolated Hub root dirs: copy each participant into its own root
    hub0_root = str(tmp_path_factory.mktemp("hub0"))
    hub1_root = str(tmp_path_factory.mktemp("hub1"))
    shutil.copytree(
        str(workspace / "Participants" / p0_hex),
        str(pathlib.Path(hub0_root) / "Participants" / p0_hex),
    )
    shutil.copytree(
        str(workspace / "Participants" / p1_hex),
        str(pathlib.Path(hub1_root) / "Participants" / p1_hex),
    )

    ep0 = f"http://localhost:{HUB_A_PORT}"
    ep1 = f"http://localhost:{HUB_B_PORT}"

    proc0 = _start_hub(hub0_root, HUB_A_PORT)
    proc1 = _start_hub(hub1_root, HUB_B_PORT)
    try:
        _wait_for_hub(ep0)
        _wait_for_hub(ep1)

        if proc0.poll() is not None:
            raise RuntimeError(f"Hub 0 exited early (code {proc0.returncode})")
        if proc1.poll() is not None:
            raise RuntimeError(f"Hub 1 exited early (code {proc1.returncode})")

        yield {
            "team_name": team_name,
            "p0_hex": p0_hex, "p0_nick": p0_nick, "p0_member_id": p0_member_id,
            "p1_hex": p1_hex, "p1_nick": p1_nick, "p1_member_id": p1_member_id,
            "ep0": ep0, "ep1": ep1,
        }
    finally:
        proc0.terminate()
        proc1.terminate()
        proc0.wait()
        proc1.wait()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_dropbox_ping_pong(dropbox_env, tmp_path):
    """Alice (p0) pings, Bob (p1) pongs. Measures round-trip latency over Dropbox."""
    team = dropbox_env["team_name"]
    p0_hex, p0_nick = dropbox_env["p0_hex"], dropbox_env["p0_nick"]
    p1_hex, p1_nick = dropbox_env["p1_hex"], dropbox_env["p1_nick"]
    p0_member_id = dropbox_env["p0_member_id"]
    p1_member_id = dropbox_env["p1_member_id"]
    ep0, ep1 = dropbox_env["ep0"], dropbox_env["ep1"]

    print(f"\nParticipants: {p0_nick} ({p0_hex[:8]}…) and {p1_nick} ({p1_hex[:8]}…)")
    print(f"Team: {team}")

    # Open sessions
    tok0 = _open_session(ep0, p0_nick, team)
    tok1 = _open_session(ep1, p1_nick, team)

    # Ensure cloud buckets are ready
    for ep, tok in [(ep0, tok0), (ep1, tok1)]:
        resp = requests.post(
            f"{ep}/cloud/setup",
            headers={"Authorization": f"Bearer {tok}"},
        )
        resp.raise_for_status()

    # Create vaults in fresh temp dirs
    vault0 = str(tmp_path / "vault-p0")
    vault1 = str(tmp_path / "vault-p1")
    init_vault(vault0, p0_hex)
    init_vault(vault1, p1_hex)

    # Remotes
    remote0 = CS.SmallSeaRemote(tok0, base_url=ep0)
    remote1 = CS.SmallSeaRemote(tok1, base_url=ep1)
    p1_reads_p0 = CS.PeerSmallSeaRemote(tok1, p0_member_id, base_url=ep1)
    p0_reads_p1 = CS.PeerSmallSeaRemote(tok0, p1_member_id, base_url=ep0)

    # p0 creates niche, pushes registry; p1 discovers via registry pull
    create_niche(vault0, p0_hex, team, NICHE)
    push_registry(vault0, p0_hex, team, remote0)
    pull_registry(vault1, p1_hex, team, p1_reads_p0)

    # p1 pulls initial (empty) niche so its git dir is initialised
    pull_niche(vault1, p1_hex, team, NICHE, p1_reads_p0)

    # Set up checkouts
    co0 = tmp_path / "checkout-p0"
    co1 = tmp_path / "checkout-p1"
    add_checkout(vault0, p0_hex, team, NICHE, str(co0))
    add_checkout(vault1, p1_hex, team, NICHE, str(co1))

    # Snapshot etags before the ping
    etag_p0_before = _get_signal_etag(ep1, tok1, p0_member_id)
    etag_p1_before = _get_signal_etag(ep0, tok0, p1_member_id)

    # ---- PING: p0 writes and pushes ----
    t_start = time.time()
    (co0 / "ping.txt").write_text(f"ping {t_start}\n")
    publish(vault0, p0_hex, team, NICHE, str(co0), message="ping")
    push_niche(vault0, p0_hex, team, NICHE, remote0)

    # ---- p1 detects, pulls, writes pong, pushes ----
    _poll_for_signal_change(ep1, tok1, p0_member_id, etag_p0_before)
    t_p1_received = time.time()

    pull_niche(vault1, p1_hex, team, NICHE, p1_reads_p0)
    assert (co1 / "ping.txt").exists(), f"{p1_nick} should see ping.txt after pull"

    (co1 / "pong.txt").write_text(f"pong {t_p1_received}\n")
    publish(vault1, p1_hex, team, NICHE, str(co1), message="pong")
    push_niche(vault1, p1_hex, team, NICHE, remote1)

    # ---- p0 detects and pulls pong ----
    _poll_for_signal_change(ep0, tok0, p1_member_id, etag_p1_before)
    t_p0_received = time.time()

    pull_niche(vault0, p0_hex, team, NICHE, p0_reads_p1)
    assert (co0 / "pong.txt").exists(), f"{p0_nick} should see pong.txt after pull"

    # ---- Report ----
    one_way_ms = (t_p1_received - t_start) * 1000
    round_trip_ms = (t_p0_received - t_start) * 1000
    print(f"\n=== Dropbox Ping-Pong Latency ===")
    print(f"  {p0_nick} → push → {p1_nick} detects: {one_way_ms:.0f} ms")
    print(f"  Full round trip ({p0_nick} → {p1_nick} → {p0_nick}): {round_trip_ms:.0f} ms")
