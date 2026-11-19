#!/usr/bin/env python3
"""Two teammates, two CalDAV servers, syncing through their Hubs when asked.

Simulates two computers: each teammate has their own Hub process and data
directory, plus their own MinIO server standing in for their cloud storage.
Runs one CalDAV spike server per teammate:
    Alice: http://localhost:5232/  user alice, password tide
    Bob:   http://localhost:5233/  user bob,   password tide
Type `a` to sync Alice, `b` to sync Bob, `q` to quit.

Syncing a teammate publishes their calendar, fetches the other's, merges,
and publishes again if the merge produced anything new.

The data directory is a sandbox workspace (devtools/sandbox). The first run
sets up a team with Alice and Bob (the setup from tests/test_hub_sync.py).
Later runs keep the team and the calendars: they start whatever MinIO servers
and Hubs are not already running on the ports in sandbox.json, and use any
that are. To look at the teammates' Manager UIs, open the same workspace in
the sandbox dashboard:
    uv run small-sea-sandbox --workspace Scratch/tide-table-demo
`--fresh` resets a completed demo workspace.

Run:
    uv run python packages/tide-table/spikes/two_teammates_demo.py
"""

import argparse
import json
import logging
import pathlib
import shutil
import subprocess
import sys
import threading
import time

import httpx

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path[:0] = [str(REPO_ROOT), str(HERE.parent / "tests"), str(HERE), str(REPO_ROOT / "devtools" / "sandbox")]

from cod_sync.protocol import CodSyncError, NoPublishedHeadError  # noqa: E402
from cod_sync.repo import RepoError  # noqa: E402
from cod_sync.store import PeerSmallSeaStore, SmallSeaStore, StoreError  # noqa: E402
from sandbox.workspace import SandboxWorkspace, is_port_in_use  # noqa: E402
from test_support import _shut_down  # noqa: E402

from caldav_spike import make_server  # noqa: E402
from test_hub_sync import _setup_two_teammate_team  # noqa: E402
from tide_table.calendar_repo import EventConflict, peer_ref  # noqa: E402
from tide_table.hub_signer import HubSigner, UnauthorizedHistory  # noqa: E402

DATA = REPO_ROOT / "Scratch" / "tide-table-demo"
PORTS = {"Alice": 5232, "Bob": 5233}
PASSWORD = "tide"
STATE = "tide-table.json"  # teammate IDs, beside sandbox.json


def ensure_running(port, start, procs, ready_url):
    """Start or reuse a server and wait for its readiness endpoint to succeed."""
    proc = None
    if not is_port_in_use(port):
        proc = start()
        procs.append(proc)
    deadline = time.monotonic() + 30
    while True:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"server for port {port} exited (code {proc.returncode})")
        try:
            status = httpx.get(ready_url, timeout=0.5).status_code
            if status == 200:
                return
            if proc is None and status < 500:
                raise RuntimeError(f"Port {port} is in use, but {ready_url} returned HTTP {status}")
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"server did not become ready at {ready_url}")
        time.sleep(0.1)


def start_minio(ws, server, procs):
    ensure_running(server.api_port,
                   lambda: ws.start_minio(server, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                   procs, f"http://127.0.0.1:{server.api_port}/minio/health/live")
    return f"http://127.0.0.1:{server.api_port}"


def start_hub(ws, p, procs):
    with open(ws.workspace_dir / f"{p.root}-hub.log", "ab") as log:
        ensure_running(p.hub_port, lambda: ws.start_hub(p, auto_approve=True, stdout=log, stderr=subprocess.STDOUT),
                       procs, f"http://127.0.0.1:{p.hub_port}/sessions/pending")
    return f"http://127.0.0.1:{p.hub_port}"


def open_session(hub, name):
    """Open a TideTable session; on a Hub that does not auto-approve, read the PIN as the sandbox dashboard does."""
    http = httpx.Client(base_url=hub, timeout=60)
    resp = http.post("/sessions/request", json={
        "participant": name, "app": "TideTable", "team": "ProjectX", "client": "Tide Table demo", "mode": "encrypted"})
    resp.raise_for_status()
    result = resp.json()
    if "token" in result:
        return result["token"]
    pending = http.get("/sessions/pending").json()
    pin = next(s["pin"] for s in pending if s["pending_id"] == result["pending_id"])
    resp = http.post("/sessions/confirm", json={"pending_id": result["pending_id"], "pin": pin})
    resp.raise_for_status()
    return resp.json()


def set_up_team(ws, procs):
    """First run: create Alice and Bob in the workspace and set up their team."""
    names = {name.lower(): name for name in PORTS}
    accounts = iter(names)

    def minio_gen():
        label = next(accounts)
        server = ws.add_minio_server()
        endpoint = start_minio(ws, server, procs)
        account = ws.create_account(server.api_port, label)
        return {"endpoint": endpoint, "access_key": account.access_key, "secret_key": account.secret_key}

    def hub_gen(root):
        (participant_dir,) = (root / "Participants").iterdir()
        p = ws.register_participant(names[root.name], root.name, participant_dir.name)
        return p.hub_port, start_hub(ws, p, procs)

    team = _setup_two_teammate_team(ws.workspace_dir, minio_gen, hub_gen)
    (ws.workspace_dir / STATE).write_text(json.dumps(
        {name: {"teammate_id": t["teammate_id"]} for name, t in team.items()}, indent=1))
    return team


def resume_team(ws, procs):
    """Later runs: start what is not running, and open new sessions."""
    state = json.loads((ws.workspace_dir / STATE).read_text())
    team = {}
    for name in PORTS:
        p = next(p for p in ws.participants if p.nickname == name)
        account = next(a for a in ws.minio_accounts if a.label == p.root)
        start_minio(ws, next(s for s in ws.minio_servers if s.api_port == account.server_port), procs)
        hub = start_hub(ws, p, procs)
        team[name] = {"hub": hub, "token": open_session(hub, name), "teammate_id": state[name]["teammate_id"]}
    return team


def sync(me, them, stores, team):
    """Sync one teammate through their own Hub; team[name] has hub, token, teammate_id."""
    store = stores[me]
    cal = store.cal
    hub, token, them_id = team[me]["hub"], team[me]["token"], team[them]["teammate_id"]
    with store.lock:
        before = store.snapshot()
        own = SmallSeaStore(token, base_url=hub)
        if cal.repo.has_commits():
            cal.publish(own)
        try:
            cal.fetch(PeerSmallSeaStore(token, them_id, base_url=hub), them_id)
        except NoPublishedHeadError:
            print(f"{them} has not published anything yet")
            return
        head = cal.repo.head()
        try:
            merged = cal.merge(peer_ref(them_id))
        except EventConflict as e:
            print(f"{me}: not merged; these events changed on both sides in ways that conflict: {e.paths}")
            return
        changed, deleted = store.record_changes_since(before)
        if cal.repo.head() != head:
            cal.publish(own)
    print(f"{me} synced with {them}: {len(changed)} changed, {len(deleted)} deleted"
          + (f", merged event by event: {merged}" if merged else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alice-port", type=int, default=PORTS["Alice"])
    ap.add_argument("--bob-port", type=int, default=PORTS["Bob"])
    ap.add_argument("--data", type=pathlib.Path, default=DATA,
                    help=f"sandbox workspace for the demo (default: {DATA})")
    ap.add_argument("--fresh", action="store_true", help="reset a completed demo workspace")
    args = ap.parse_args()
    path = args.data.expanduser().absolute()
    data = path.resolve()
    if args.fresh and path != data:
        ap.error("--fresh requires a direct path to the demo workspace")
    if data.exists() and not data.is_dir():
        ap.error(f"{data} is not a directory")
    resuming = (data / STATE).is_file()
    if not resuming and data.exists() and any(data.iterdir()):
        ap.error(f"{data} has data but no {STATE}. Choose an empty directory with --data.")
    if args.fresh and resuming:
        shutil.rmtree(data)
        resuming = False
    PORTS.update(Alice=args.alice_port, Bob=args.bob_port)
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    data.mkdir(parents=True, exist_ok=True)
    ws = SandboxWorkspace.load(data)
    procs = []
    try:
        team = resume_team(ws, procs) if resuming else set_up_team(ws, procs)
        for name, t in team.items():
            print(f"{name}'s Hub: {t['hub']}")
        stores = {}
        for name, port in PORTS.items():
            # Not inside the participant's root: the Hub verifies no git directory under its own root.
            server = make_server(port, name.lower(), PASSWORD, str(data / "tide-table" / name.lower()),
                                 signer=HubSigner(team[name]["hub"], team[name]["token"]))
            stores[name] = server.store
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print("Ready. Type a (sync Alice), b (sync Bob), q (quit).")
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd in ("a", "b"):
                me, them = ("Alice", "Bob") if cmd == "a" else ("Bob", "Alice")
                try:
                    sync(me, them, stores, team)
                except (CodSyncError, StoreError, RepoError, UnauthorizedHistory,
                        subprocess.CalledProcessError) as e:
                    print(f"{me}: sync failed: {e}", file=sys.stderr)
            elif cmd == "q":
                break
            elif cmd:
                print("a, b or q")
    finally:
        for proc in procs:
            _shut_down(proc)


if __name__ == "__main__":
    main()
