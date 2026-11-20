#!/usr/bin/env python3
"""Two teammates, two CalDAV servers, syncing through their Hubs on their own.

Simulates two computers: each teammate has their own Hub process and data
directory, plus their own MinIO server standing in for their cloud storage.
Runs one CalDAV spike server per teammate:
    Alice: http://localhost:5232/  user alice, password tide
    Bob:   http://localhost:5233/  user bob,   password tide
Type `q` to quit.

Each teammate's server has a sync agent (tide_table/sync_agent.py). It
publishes after every local change, and when the Hub reports that the other
teammate published, it fetches their history into a parked ref. The server
then merges parked changes as it gets to them and publishes the result.
Changes that do not merge show up in the calendar as a placeholder event
linking to a page for choosing a side (see caldav_spike.py).
Hubs this demo starts check teammates' signals every 5 seconds, so changes
arrive within about that long; a Hub started elsewhere uses its own interval.

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
import asyncio
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import time

import httpx

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path[:0] = [str(REPO_ROOT), str(HERE.parent / "tests"), str(HERE), str(REPO_ROOT / "devtools" / "sandbox")]

from cod_sync.repo import RepoError  # noqa: E402
from sandbox.workspace import SandboxWorkspace, is_port_in_use  # noqa: E402
from test_support import _shut_down  # noqa: E402

from caldav_spike import make_server  # noqa: E402
from test_hub_sync import _setup_two_teammate_team  # noqa: E402
from tide_table.calendar_repo import EventConflict  # noqa: E402
from tide_table.hub_signer import HubSigner  # noqa: E402
from tide_table.sync_agent import WATCH_TIMEOUT, SyncAgent, SyncFailed  # noqa: E402

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
    # Every teammate's storage first: a Hub's first watcher pass reads its
    # teammates' storage, and while that storage is unreachable the Hub
    # answers no requests.
    for server in ws.minio_servers:
        start_minio(ws, server, procs)
    for name in PORTS:
        p = next(p for p in ws.participants if p.nickname == name)
        hub = start_hub(ws, p, procs)
        team[name] = {"hub": hub, "token": open_session(hub, name), "teammate_id": state[name]["teammate_id"]}
    return team


async def process_events(name, store, agent, names):
    """The app's side: take the agent's events one at a time."""
    while True:
        await handle_event(name, store, agent, names, await agent.events.get())


async def handle_event(name, store, agent, names, event):
    """Merge a teammate's parked changes, record them for CalDAV clients, and ask to publish.

    The merge holds the store's lock, so it waits for any CalDAV write in
    progress and CalDAV requests wait for it.
    """
    if isinstance(event, SyncFailed):
        print(f"{name}: {event.what} failed, will retry: {event.error}", file=sys.stderr)
        return
    them = names.get(event.teammate_id, event.teammate_id[:8])
    await store.lock.acquire()
    # Its own task, because cancelling a merge halfway would leave git
    # mid-merge. A worker cancelled meanwhile waits for the merge, and ends
    # with the merge's error if it failed, so stop_workers can report it.
    merge = asyncio.create_task(_merge_holding_lock(store, event.teammate_id))
    try:
        merged, changed, deleted, moved = await asyncio.shield(merge)
    except asyncio.CancelledError:
        try:
            await merge
        except (EventConflict, RepoError) as e:
            print(f"{name}: {them}'s changes stay parked, not merged: {e}")
        raise
    except EventConflict as e:
        print(f"{name}: {them}'s changes stay parked, not merged: {e}\n"
              f"  resolve at {store.base_url}{store.conflict_page}")
        return
    except RepoError as e:
        print(f"{name}: {them}'s changes stay parked, not merged: {e}")
        return
    if moved:
        agent.request_publish()
    if moved or changed or deleted:
        print(f"{name} merged {them}'s changes: {len(changed)} changed, {len(deleted)} deleted"
              + (f", merged event by event: {merged}" if merged else ""))


async def _merge_holding_lock(store, teammate_id):
    """Merge, record the changes, and release the store's lock the caller took."""
    try:
        return await store.merge(teammate_id)
    finally:
        store.lock.release()


async def run_teammates(team, data):
    """Serve each teammate's calendar and sync it until the user quits; then stop it all."""
    names = {t["teammate_id"]: name for name, t in team.items()}
    clients, servers, serving, agents, tasks = [], [], [], [], []
    try:
        for name, port in PORTS.items():
            hub, token = team[name]["hub"], team[name]["token"]
            http = httpx.AsyncClient(base_url=hub, timeout=WATCH_TIMEOUT + 30)
            clients.append(http)
            # Not inside the participant's root: the Hub verifies no git directory under its own root.
            server = await make_server(port, name.lower(), PASSWORD, str(data / "tide-table" / name.lower()),
                                       signer=await HubSigner.open(hub, token, http))
            agent = SyncAgent(server.store.cal, http, token)
            server.store.on_commit = agent.request_publish
            server.store.teammate_names = names
            tasks.append(asyncio.create_task(process_events(name, server.store, agent, names)))
            agents.append(agent)
            agent.start()
            servers.append(server)
            serving.append(asyncio.create_task(server.serve()))
            await until_started(server, serving[-1])
        print("Ready. Type q to quit.")
        quit = asyncio.create_task(wait_for_quit())
        tasks.append(quit)
        # Event workers and servers should never return; if one fails, its
        # teammate would stop merging or serving, so stop the demo and show the error.
        done, _ = await asyncio.wait(tasks + serving, return_when=asyncio.FIRST_COMPLETED)
        for task in done - {quit}:
            task.result()
    finally:
        # Event workers first, so no merge starts; then CalDAV, so no commit
        # asks a stopped agent to publish; then the agents, so none is waiting
        # on a Hub when the Hubs shut down. Parked changes not yet merged are
        # fetched and reported again next run.
        failures = await stop_workers(tasks)
        await stop_caldav(servers, serving)
        for agent in agents:
            await agent.stop()
        for http in clients:
            await http.aclose()
        if failures:
            raise failures[0]


async def stop_workers(tasks):
    """Cancel the event workers and wait for them; return the errors they ended with.

    A worker cancelled during a merge finishes the merge first, and ends with
    the merge's error if it failed.
    """
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, Exception)]


async def until_started(server, serve):
    """Wait until the server listens; raise if it fails first, as when its port is taken."""
    while not server.started:
        if serve.done():
            serve.result()
            raise RuntimeError("the CalDAV server stopped before it started")
        await asyncio.sleep(0.05)


async def stop_caldav(servers, serving):
    """Stop accepting CalDAV requests, and wait for requests already under way.

    A write in progress commits, and committing asks the Hub to sign, so it
    must finish while the Hub is still up. uvicorn closes idle connections at
    once and waits for the others to finish their requests.
    """
    for server in servers:
        server.should_exit = True
    await asyncio.gather(*serving)


async def wait_for_quit():
    """Return when stdin says q, or closes."""
    loop = asyncio.get_running_loop()
    lines = asyncio.Queue()
    loop.add_reader(sys.stdin, lambda: lines.put_nowait(sys.stdin.readline()))
    try:
        while (line := await lines.get()) and line.strip().lower() != "q":
            pass
    finally:
        loop.remove_reader(sys.stdin)


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
    os.environ.setdefault("SMALL_SEA_WATCHER_INTERVAL", "5")  # for the Hubs this demo starts
    procs = []
    try:
        team = resume_team(ws, procs) if resuming else set_up_team(ws, procs)
        for name, t in team.items():
            print(f"{name}'s Hub: {t['hub']}")
        asyncio.run(run_teammates(team, data))
    finally:
        for proc in reversed(procs):  # Hubs before the storage they use
            _shut_down(proc)


if __name__ == "__main__":
    main()
