#!/usr/bin/env python3
"""Two teammates, two CalDAV servers, syncing through their Hubs when asked.

Simulates two computers: each teammate has their own Hub process and data
directory, plus their own MinIO server standing in for their cloud storage.
Sets up a team with Alice and Bob (the setup from tests/test_hub_sync.py),
then runs one CalDAV spike server per teammate:
    Alice: http://localhost:5232/  user alice, password tide
    Bob:   http://localhost:5233/  user bob,   password tide
Type `a` to sync Alice, `b` to sync Bob, `q` to quit.

Syncing a teammate publishes their calendar, fetches the other's, merges,
and publishes again if the merge produced anything new.

Shortcuts beyond the test's: every run starts from scratch (new team, empty calendars); remove and re-add
the calendar accounts in the apps if they get confused by the reset.

Run:
    uv run python packages/tide-table/spikes/two_teammates_demo.py
"""

import argparse
import logging
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path[:0] = [str(REPO_ROOT), str(HERE.parent / "tests"), str(HERE)]

from cod_sync.protocol import CodSyncError, NoPublishedHeadError  # noqa: E402
from cod_sync.repo import RepoError  # noqa: E402
from cod_sync.store import PeerSmallSeaStore, SmallSeaStore, StoreError  # noqa: E402
from test_support import _shut_down, _wait_for_minio_ready, reserve_ports  # noqa: E402

from caldav_spike import make_server  # noqa: E402
from test_hub_sync import _setup_two_teammate_team, start_hub  # noqa: E402
from tide_table.calendar_repo import EventConflict, peer_ref  # noqa: E402

DATA = REPO_ROOT / "Scratch" / "tide-table-demo"
PORTS = {"Alice": 5232, "Bob": 5233}
PASSWORD = "tide"


def start_minio(root_dir, procs):
    port, console_port = reserve_ports([None, None])
    access_key, secret_key = secrets.token_hex(16), secrets.token_hex(32)
    env = dict(os.environ, MINIO_ROOT_USER=access_key, MINIO_ROOT_PASSWORD=secret_key)
    root_dir.mkdir(parents=True)
    proc = subprocess.Popen(
        ["minio", "server", str(root_dir), "--address", f"127.0.0.1:{port}",
         "--console-address", f"127.0.0.1:{console_port}"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(proc)
    endpoint = f"http://127.0.0.1:{port}"
    _wait_for_minio_ready(proc, endpoint, access_key, secret_key)
    return {"port": port, "endpoint": endpoint, "access_key": access_key, "secret_key": secret_key}


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
                    help=f"demo data directory, deleted at start (default: {DATA})")
    args = ap.parse_args()
    data = args.data
    PORTS.update(Alice=args.alice_port, Bob=args.bob_port)
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    shutil.rmtree(data, ignore_errors=True)
    procs = []
    try:
        minio_dirs = iter(["minio-alice", "minio-bob"])
        team = _setup_two_teammate_team(
            data / "hubs", lambda: start_minio(data / next(minio_dirs), procs),
            lambda root: start_hub(root, procs, log_path=root.parent / f"{root.name}-hub.log"))
        for name, t in team.items():
            print(f"{name}'s Hub: {t['hub']}")
        stores = {}
        for name, port in PORTS.items():
            server = make_server(port, name.lower(), PASSWORD, str(data / name.lower()))
            stores[name] = server.store
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print("Ready. Type a (sync Alice), b (sync Bob), q (quit).")
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd in ("a", "b"):
                me, them = ("Alice", "Bob") if cmd == "a" else ("Bob", "Alice")
                try:
                    sync(me, them, stores, team)
                except (CodSyncError, StoreError, RepoError, subprocess.CalledProcessError) as e:
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
