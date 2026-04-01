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
HUB_A_NTFY_PORT = 11702
HUB_B_NTFY_PORT = 11703
NICHE = "ping-pong"
HUB_STARTUP_TIMEOUT = 30   # seconds
SIGNAL_POLL_TIMEOUT = 120  # seconds to wait for peer signal

# Repo root: packages/shared-file-vault/tests/ → up three levels
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent


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


def _discover_shared_team_name(workspace: pathlib.Path, hexes: list[str]) -> str:
    """Find the team name present in all participants' directories (excluding NoteToSelf)."""
    def _teams(hex_):
        p_dir = workspace / "Participants" / hex_
        return {d.name for d in p_dir.iterdir() if d.is_dir() and d.name != "NoteToSelf"}

    shared = _teams(hexes[0])
    for hex_ in hexes[1:]:
        shared &= _teams(hex_)

    if len(shared) == 0:
        raise ValueError(f"No shared team found among participants {hexes}")
    if len(shared) > 1:
        raise ValueError(
            f"Multiple shared teams found among participants {hexes}: {shared}. "
            "Set SMALL_SEA_DROPBOX_TEAM to specify which one."
        )
    return shared.pop()


# ---------------------------------------------------------------------------
# Hub lifecycle helpers
# ---------------------------------------------------------------------------


def _start_hub(
    root_dir: str, port: int, log_path: pathlib.Path,
    watcher_interval: int = 2,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["SMALL_SEA_ROOT_DIR"] = root_dir
    env["SMALL_SEA_AUTO_APPROVE_SESSIONS"] = "1"
    env["SMALL_SEA_LOG_LEVEL"] = "DEBUG"
    env["SMALL_SEA_WATCHER_INTERVAL"] = str(watcher_interval)
    cmd = [
        "uv", "run", "fastapi", "dev",
        "packages/small-sea-hub/small_sea_hub/server.py",
        "--port", str(port),
    ]
    log_fh = open(log_path, "w")
    return subprocess.Popen(
        cmd, env=env, stdout=log_fh, stderr=log_fh, cwd=str(_REPO_ROOT)
    )


def _tail(path: pathlib.Path, lines: int = 40) -> str:
    """Return the last N lines of a file, or a placeholder if unreadable."""
    try:
        text = path.read_text(errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return f"(could not read {path})"


def _wait_for_hub(
    endpoint: str, proc: subprocess.Popen, log_path: pathlib.Path,
    timeout: int = HUB_STARTUP_TIMEOUT,
) -> None:
    """Poll until Hub responds. On failure, show the subprocess log."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"Hub at {endpoint} exited early (code {proc.returncode}).\n"
                f"--- last lines of {log_path} ---\n{_tail(log_path)}"
            )
        try:
            resp = requests.get(f"{endpoint}/docs", timeout=2)
            if resp.status_code < 500:
                return
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"Hub at {endpoint} did not respond within {timeout}s.\n"
        f"--- last lines of {log_path} ---\n{_tail(log_path)}"
    )


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
# Push notification helpers
# ---------------------------------------------------------------------------


def _get_peer_count(endpoint: str, token: str, member_id_hex: str) -> int:
    """Return the current max signal count for a peer, or 0 if not yet present."""
    resp = requests.get(
        f"{endpoint}/peer_signal",
        params={"member_id": member_id_hex},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    if resp.status_code == 404:
        return 0
    resp.raise_for_status()
    stations = resp.json().get("stations", {})
    return max(stations.values(), default=0)


def _wait_for_notification(
    endpoint: str, token: str, member_id_hex: str,
    known_count: int, timeout: int = SIGNAL_POLL_TIMEOUT,
) -> int:
    """Long-poll until Hub watcher detects a new signal count for the peer.

    Returns the new count.  Raises TimeoutError if the Hub does not report a
    change within *timeout* seconds.
    """
    resp = requests.post(
        f"{endpoint}/notifications/watch",
        json={"known": {member_id_hex: known_count}, "timeout": timeout},
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout + 10,
    )
    resp.raise_for_status()
    updated = resp.json().get("updated", {})
    if member_id_hex not in updated:
        raise TimeoutError(
            f"No push notification for {member_id_hex[:8]}… within {timeout}s"
        )
    return updated[member_id_hex]


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

    team_name = (
        os.environ.get("SMALL_SEA_DROPBOX_TEAM")
        or _discover_shared_team_name(workspace, hexes)
    )
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

    tmp = tmp_path_factory.mktemp("hub_logs")
    log0 = tmp / f"hub_{HUB_A_PORT}.log"
    log1 = tmp / f"hub_{HUB_B_PORT}.log"

    proc0 = _start_hub(hub0_root, HUB_A_PORT, log0)
    proc1 = _start_hub(hub1_root, HUB_B_PORT, log1)
    try:
        _wait_for_hub(ep0, proc0, log0)
        _wait_for_hub(ep1, proc1, log1)

        yield {
            "team_name": team_name,
            "p0_hex": p0_hex, "p0_nick": p0_nick, "p0_member_id": p0_member_id,
            "p1_hex": p1_hex, "p1_nick": p1_nick, "p1_member_id": p1_member_id,
            "ep0": ep0, "ep1": ep1,
            "log0": log0, "log1": log1,
        }
    finally:
        proc0.terminate()
        proc1.terminate()
        proc0.wait()
        proc1.wait()


@pytest.fixture(scope="module")
def dropbox_ntfy_env(tmp_path_factory):
    """Like dropbox_env but with ntfy configured and polling effectively disabled.

    Requires SMALL_SEA_DROPBOX_WORKSPACE and SMALL_SEA_NTFY_URL to be set.
    Hubs start with SMALL_SEA_WATCHER_INTERVAL=300 so any fast notification
    must come from ntfy, not the polling watcher.
    """
    workspace_str = os.environ.get("SMALL_SEA_DROPBOX_WORKSPACE")
    ntfy_url = os.environ.get("SMALL_SEA_NTFY_URL")
    if not workspace_str:
        pytest.skip("SMALL_SEA_DROPBOX_WORKSPACE not set — skipping ntfy latency test")
    if not ntfy_url:
        pytest.skip("SMALL_SEA_NTFY_URL not set — skipping ntfy latency test")

    workspace = pathlib.Path(workspace_str).expanduser().resolve()
    if not workspace.exists():
        pytest.skip(f"SMALL_SEA_DROPBOX_WORKSPACE path does not exist: {workspace}")

    hexes = _discover_participants(workspace)
    p0_hex, p1_hex = hexes

    team_name = (
        os.environ.get("SMALL_SEA_DROPBOX_TEAM")
        or _discover_shared_team_name(workspace, hexes)
    )
    p0_nick = _get_nickname(workspace, p0_hex)
    p1_nick = _get_nickname(workspace, p1_hex)
    p0_member_id = _get_member_id_hex(workspace, p0_hex, team_name)
    p1_member_id = _get_member_id_hex(workspace, p1_hex, team_name)

    # Isolated Hub root dirs
    hub0_root = pathlib.Path(tmp_path_factory.mktemp("hub0_ntfy"))
    hub1_root = pathlib.Path(tmp_path_factory.mktemp("hub1_ntfy"))
    shutil.copytree(
        str(workspace / "Participants" / p0_hex),
        str(hub0_root / "Participants" / p0_hex),
    )
    shutil.copytree(
        str(workspace / "Participants" / p1_hex),
        str(hub1_root / "Participants" / p1_hex),
    )

    # Configure ntfy in each copied workspace so the Hub publishes/subscribes.
    from small_sea_manager.manager import TeamManager
    for hub_root, participant_hex in [(hub0_root, p0_hex), (hub1_root, p1_hex)]:
        TeamManager(str(hub_root), participant_hex).set_notification_service("ntfy", ntfy_url)

    ep0 = f"http://localhost:{HUB_A_NTFY_PORT}"
    ep1 = f"http://localhost:{HUB_B_NTFY_PORT}"

    tmp = tmp_path_factory.mktemp("hub_ntfy_logs")
    log0 = tmp / f"hub_{HUB_A_NTFY_PORT}.log"
    log1 = tmp / f"hub_{HUB_B_NTFY_PORT}.log"

    # Watcher interval = 300s — polling is effectively disabled.
    # Any fast notification must come from ntfy.
    proc0 = _start_hub(str(hub0_root), HUB_A_NTFY_PORT, log0, watcher_interval=300)
    proc1 = _start_hub(str(hub1_root), HUB_B_NTFY_PORT, log1, watcher_interval=300)
    try:
        _wait_for_hub(ep0, proc0, log0)
        _wait_for_hub(ep1, proc1, log1)

        yield {
            "team_name": team_name,
            "p0_hex": p0_hex, "p0_nick": p0_nick, "p0_member_id": p0_member_id,
            "p1_hex": p1_hex, "p1_nick": p1_nick, "p1_member_id": p1_member_id,
            "ep0": ep0, "ep1": ep1,
            "log0": log0, "log1": log1,
            "ntfy_url": ntfy_url,
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

    # Remotes — each repo gets its own path prefix so it doesn't collide
    # with the team sync repo or other vaults in the same Dropbox bucket.
    reg_pfx = f"vault/{team}/registry/"
    niche_pfx = f"vault/{team}/niches/{NICHE}/"

    reg_remote0 = CS.SmallSeaRemote(tok0, base_url=ep0, path_prefix=reg_pfx)
    reg_remote1 = CS.SmallSeaRemote(tok1, base_url=ep1, path_prefix=reg_pfx)
    niche_remote0 = CS.SmallSeaRemote(tok0, base_url=ep0, path_prefix=niche_pfx)
    niche_remote1 = CS.SmallSeaRemote(tok1, base_url=ep1, path_prefix=niche_pfx)
    p1_reads_p0_reg = CS.PeerSmallSeaRemote(tok1, p0_member_id, base_url=ep1, path_prefix=reg_pfx)
    p1_reads_p0_niche = CS.PeerSmallSeaRemote(tok1, p0_member_id, base_url=ep1, path_prefix=niche_pfx)
    p0_reads_p1_niche = CS.PeerSmallSeaRemote(tok0, p1_member_id, base_url=ep0, path_prefix=niche_pfx)

    # p0 creates niche and makes an initial commit so p1 has something to clone
    create_niche(vault0, p0_hex, team, NICHE)
    co0 = tmp_path / "checkout-p0"
    add_checkout(vault0, p0_hex, team, NICHE, str(co0))
    (co0 / "init.txt").write_text("initialised\n")
    publish(vault0, p0_hex, team, NICHE, str(co0), message="init")
    push_niche(vault0, p0_hex, team, NICHE, niche_remote0)

    # p0 pushes registry; p1 discovers via registry pull
    push_registry(vault0, p0_hex, team, reg_remote0)
    pull_registry(vault1, p1_hex, team, p1_reads_p0_reg)

    # p1 clones the niche (now has at least one commit) and sets up its checkout
    pull_niche(vault1, p1_hex, team, NICHE, p1_reads_p0_niche)
    co1 = tmp_path / "checkout-p1"
    add_checkout(vault1, p1_hex, team, NICHE, str(co1))

    # Snapshot etags before the ping
    etag_p0_before = _get_signal_etag(ep1, tok1, p0_member_id)
    etag_p1_before = _get_signal_etag(ep0, tok0, p1_member_id)

    # ---- PING: p0 writes and pushes ----
    t_start = time.time()
    (co0 / "ping.txt").write_text(f"ping {t_start}\n")
    publish(vault0, p0_hex, team, NICHE, str(co0), message="ping")
    push_niche(vault0, p0_hex, team, NICHE, niche_remote0)

    # ---- p1 detects, pulls, writes pong, pushes ----
    _poll_for_signal_change(ep1, tok1, p0_member_id, etag_p0_before)
    t_p1_received = time.time()

    pull_niche(vault1, p1_hex, team, NICHE, p1_reads_p0_niche)
    assert (co1 / "ping.txt").exists(), f"{p1_nick} should see ping.txt after pull"

    (co1 / "pong.txt").write_text(f"pong {t_p1_received}\n")
    publish(vault1, p1_hex, team, NICHE, str(co1), message="pong")
    push_niche(vault1, p1_hex, team, NICHE, niche_remote1)

    # ---- p0 detects and pulls pong ----
    _poll_for_signal_change(ep0, tok0, p1_member_id, etag_p1_before)
    t_p0_received = time.time()

    pull_niche(vault0, p0_hex, team, NICHE, p0_reads_p1_niche)
    assert (co0 / "pong.txt").exists(), f"{p0_nick} should see pong.txt after pull"

    # ---- Report ----
    one_way_ms = (t_p1_received - t_start) * 1000
    round_trip_ms = (t_p0_received - t_start) * 1000
    print(f"\n=== Dropbox Ping-Pong Latency (polling) ===")
    print(f"  {p0_nick} → push → {p1_nick} detects: {one_way_ms:.0f} ms")
    print(f"  Full round trip ({p0_nick} → {p1_nick} → {p0_nick}): {round_trip_ms:.0f} ms")


def test_dropbox_ping_pong_push(dropbox_env, tmp_path):
    """Like test_dropbox_ping_pong but uses /notifications/watch instead of polling.

    The Hub's watcher (SMALL_SEA_WATCHER_INTERVAL=2s) detects changes and
    wakes the long-poll call, so the test client never hammers /peer_signal.
    """
    team = dropbox_env["team_name"]
    p0_hex, p0_nick = dropbox_env["p0_hex"], dropbox_env["p0_nick"]
    p1_hex, p1_nick = dropbox_env["p1_hex"], dropbox_env["p1_nick"]
    p0_member_id = dropbox_env["p0_member_id"]
    p1_member_id = dropbox_env["p1_member_id"]
    ep0, ep1 = dropbox_env["ep0"], dropbox_env["ep1"]

    print(f"\nParticipants: {p0_nick} ({p0_hex[:8]}…) and {p1_nick} ({p1_hex[:8]}…)")
    print(f"Team: {team}")

    tok0 = _open_session(ep0, p0_nick, team)
    tok1 = _open_session(ep1, p1_nick, team)

    for ep, tok in [(ep0, tok0), (ep1, tok1)]:
        requests.post(
            f"{ep}/cloud/setup",
            headers={"Authorization": f"Bearer {tok}"},
        ).raise_for_status()

    vault0 = str(tmp_path / "vault-p0")
    vault1 = str(tmp_path / "vault-p1")
    init_vault(vault0, p0_hex)
    init_vault(vault1, p1_hex)

    niche_name = "ping-pong-push"
    reg_pfx = f"vault/{team}/registry-push/"
    niche_pfx = f"vault/{team}/niches/{niche_name}/"

    reg_remote0 = CS.SmallSeaRemote(tok0, base_url=ep0, path_prefix=reg_pfx)
    niche_remote0 = CS.SmallSeaRemote(tok0, base_url=ep0, path_prefix=niche_pfx)
    niche_remote1 = CS.SmallSeaRemote(tok1, base_url=ep1, path_prefix=niche_pfx)
    p1_reads_p0_reg = CS.PeerSmallSeaRemote(tok1, p0_member_id, base_url=ep1, path_prefix=reg_pfx)
    p1_reads_p0_niche = CS.PeerSmallSeaRemote(tok1, p0_member_id, base_url=ep1, path_prefix=niche_pfx)
    p0_reads_p1_niche = CS.PeerSmallSeaRemote(tok0, p1_member_id, base_url=ep0, path_prefix=niche_pfx)

    # p0 creates niche, makes initial commit, pushes
    create_niche(vault0, p0_hex, team, niche_name)
    co0 = tmp_path / "checkout-p0"
    add_checkout(vault0, p0_hex, team, niche_name, str(co0))
    (co0 / "init.txt").write_text("initialised\n")
    publish(vault0, p0_hex, team, niche_name, str(co0), message="init")
    push_niche(vault0, p0_hex, team, niche_name, niche_remote0)

    push_registry(vault0, p0_hex, team, reg_remote0)
    pull_registry(vault1, p1_hex, team, p1_reads_p0_reg)

    pull_niche(vault1, p1_hex, team, niche_name, p1_reads_p0_niche)
    co1 = tmp_path / "checkout-p1"
    add_checkout(vault1, p1_hex, team, niche_name, str(co1))

    # Snapshot counts before ping so _wait_for_notification knows the baseline.
    count_p0_before_ping = _get_peer_count(ep1, tok1, p0_member_id)
    count_p1_before_pong = _get_peer_count(ep0, tok0, p1_member_id)

    # ---- PING: p0 writes and pushes ----
    t_start = time.time()
    (co0 / "ping.txt").write_text(f"ping {t_start}\n")
    publish(vault0, p0_hex, team, niche_name, str(co0), message="ping")
    push_niche(vault0, p0_hex, team, niche_name, niche_remote0)

    # ---- p1 waits for Hub push notification, then pulls and pongs ----
    _wait_for_notification(ep1, tok1, p0_member_id, count_p0_before_ping)
    t_p1_received = time.time()

    pull_niche(vault1, p1_hex, team, niche_name, p1_reads_p0_niche)
    assert (co1 / "ping.txt").exists(), f"{p1_nick} should see ping.txt after pull"

    (co1 / "pong.txt").write_text(f"pong {t_p1_received}\n")
    publish(vault1, p1_hex, team, niche_name, str(co1), message="pong")
    push_niche(vault1, p1_hex, team, niche_name, niche_remote1)

    # ---- p0 waits for push notification of pong ----
    _wait_for_notification(ep0, tok0, p1_member_id, count_p1_before_pong)
    t_p0_received = time.time()

    pull_niche(vault0, p0_hex, team, niche_name, p0_reads_p1_niche)
    assert (co0 / "pong.txt").exists(), f"{p0_nick} should see pong.txt after pull"

    # ---- Report ----
    one_way_ms = (t_p1_received - t_start) * 1000
    round_trip_ms = (t_p0_received - t_start) * 1000
    print(f"\n=== Dropbox Ping-Pong Latency (Hub long-poll, watcher_interval=2s) ===")
    print(f"  {p0_nick} → push → {p1_nick} notified: {one_way_ms:.0f} ms")
    print(f"  Full round trip ({p0_nick} → {p1_nick} → {p0_nick}): {round_trip_ms:.0f} ms")


def test_dropbox_ping_pong_ntfy(dropbox_ntfy_env, tmp_path):
    """Ping-pong via ntfy push notifications with polling effectively disabled.

    Hubs run with SMALL_SEA_WATCHER_INTERVAL=300s. Any sub-minute round trip
    proves notifications came from ntfy, not the polling watcher.

    Requires SMALL_SEA_DROPBOX_WORKSPACE and SMALL_SEA_NTFY_URL.
    """
    team = dropbox_ntfy_env["team_name"]
    p0_hex, p0_nick = dropbox_ntfy_env["p0_hex"], dropbox_ntfy_env["p0_nick"]
    p1_hex, p1_nick = dropbox_ntfy_env["p1_hex"], dropbox_ntfy_env["p1_nick"]
    p0_member_id = dropbox_ntfy_env["p0_member_id"]
    p1_member_id = dropbox_ntfy_env["p1_member_id"]
    ep0, ep1 = dropbox_ntfy_env["ep0"], dropbox_ntfy_env["ep1"]
    ntfy_url = dropbox_ntfy_env["ntfy_url"]

    print(f"\nParticipants: {p0_nick} ({p0_hex[:8]}…) and {p1_nick} ({p1_hex[:8]}…)")
    print(f"Team: {team} | ntfy: {ntfy_url} | watcher_interval: 300s")

    tok0 = _open_session(ep0, p0_nick, team)
    tok1 = _open_session(ep1, p1_nick, team)

    for ep, tok in [(ep0, tok0), (ep1, tok1)]:
        requests.post(
            f"{ep}/cloud/setup",
            headers={"Authorization": f"Bearer {tok}"},
        ).raise_for_status()

    vault0 = str(tmp_path / "vault-p0")
    vault1 = str(tmp_path / "vault-p1")
    init_vault(vault0, p0_hex)
    init_vault(vault1, p1_hex)

    niche_name = "ping-pong-ntfy"
    reg_pfx = f"vault/{team}/registry-ntfy/"
    niche_pfx = f"vault/{team}/niches/{niche_name}/"

    reg_remote0 = CS.SmallSeaRemote(tok0, base_url=ep0, path_prefix=reg_pfx)
    niche_remote0 = CS.SmallSeaRemote(tok0, base_url=ep0, path_prefix=niche_pfx)
    niche_remote1 = CS.SmallSeaRemote(tok1, base_url=ep1, path_prefix=niche_pfx)
    p1_reads_p0_reg = CS.PeerSmallSeaRemote(tok1, p0_member_id, base_url=ep1, path_prefix=reg_pfx)
    p1_reads_p0_niche = CS.PeerSmallSeaRemote(tok1, p0_member_id, base_url=ep1, path_prefix=niche_pfx)
    p0_reads_p1_niche = CS.PeerSmallSeaRemote(tok0, p1_member_id, base_url=ep0, path_prefix=niche_pfx)

    create_niche(vault0, p0_hex, team, niche_name)
    co0 = tmp_path / "checkout-p0"
    add_checkout(vault0, p0_hex, team, niche_name, str(co0))
    (co0 / "init.txt").write_text("initialised\n")
    publish(vault0, p0_hex, team, niche_name, str(co0), message="init")
    push_niche(vault0, p0_hex, team, niche_name, niche_remote0)

    push_registry(vault0, p0_hex, team, reg_remote0)
    pull_registry(vault1, p1_hex, team, p1_reads_p0_reg)

    pull_niche(vault1, p1_hex, team, niche_name, p1_reads_p0_niche)
    co1 = tmp_path / "checkout-p1"
    add_checkout(vault1, p1_hex, team, niche_name, str(co1))

    count_p0_before_ping = _get_peer_count(ep1, tok1, p0_member_id)
    count_p1_before_pong = _get_peer_count(ep0, tok0, p1_member_id)

    def _ms(start, end=None):
        return f"{((end or time.time()) - start) * 1000:.0f} ms"

    # ---- PING ----
    t_start = time.time()
    (co0 / "ping.txt").write_text(f"ping {t_start}\n")
    publish(vault0, p0_hex, team, niche_name, str(co0), message="ping")

    t0 = time.time()
    push_niche(vault0, p0_hex, team, niche_name, niche_remote0)
    t_push_done = time.time()

    # ---- p1 waits for ntfy-driven notification ----
    _wait_for_notification(ep1, tok1, p0_member_id, count_p0_before_ping)
    t_p1_notified = time.time()

    t1 = time.time()
    pull_niche(vault1, p1_hex, team, niche_name, p1_reads_p0_niche)
    t_p1_pulled = time.time()
    assert (co1 / "ping.txt").exists(), f"{p1_nick} should see ping.txt after pull"

    (co1 / "pong.txt").write_text(f"pong {t_p1_notified}\n")
    publish(vault1, p1_hex, team, niche_name, str(co1), message="pong")

    t2 = time.time()
    push_niche(vault1, p1_hex, team, niche_name, niche_remote1)
    t_pong_push_done = time.time()

    # ---- p0 waits for ntfy-driven notification of pong ----
    _wait_for_notification(ep0, tok0, p1_member_id, count_p1_before_pong)
    t_p0_notified = time.time()

    t3 = time.time()
    pull_niche(vault0, p0_hex, team, niche_name, p0_reads_p1_niche)
    t_p0_pulled = time.time()
    assert (co0 / "pong.txt").exists(), f"{p0_nick} should see pong.txt after pull"

    # ---- Report ----
    print(f"\n=== Dropbox Ping-Pong Latency (ntfy push, watcher_interval=300s) ===")
    print(f"  {p0_nick} push_niche (ping):          {_ms(t0, t_push_done)}")
    print(f"  {p1_nick} wait_for_notification:       {_ms(t_push_done, t_p1_notified)}")
    print(f"  {p1_nick} pull_niche (ping):           {_ms(t1, t_p1_pulled)}")
    print(f"  {p1_nick} push_niche (pong):           {_ms(t2, t_pong_push_done)}")
    print(f"  {p0_nick} wait_for_notification:       {_ms(t_pong_push_done, t_p0_notified)}")
    print(f"  {p0_nick} pull_niche (pong):           {_ms(t3, t_p0_pulled)}")
    print(f"  --- one-way ({p0_nick}→{p1_nick} notified): {_ms(t_start, t_p1_notified)}")
    print(f"  --- round trip (push→push→notified):  {_ms(t_start, t_p0_notified)}")
