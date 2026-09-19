#!/usr/bin/env python3
"""Check, start or stop the local Bonsai model server.

Usage: bonsai-server.py check|up|down

check  prints the served model ids, or "down"; exits 1 when down.
up     starts the server in the background if it is not already answering,
       and waits up to 5 minutes for it to load. Needs BONSAI_HOME.
down   stops only a server that `up` started (recorded in BONSAI_JOBS_DIR).

See bonsai_env.py for settings.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import bonsai_env as B

LOAD_SECONDS = 300


def check():
    models = B.server_models()
    if models is None:
        print(f"down: nothing answers at {B.BASE_URL}")
        return 1
    print(f"up at {B.BASE_URL}: models {', '.join(map(str, models))}")
    if B.MODEL not in models:
        print(f"warning: {B.MODEL} is not among the served models")
    return 0


def up():
    if B.server_models() is not None:
        print("already running")
        return check()
    home = os.environ.get("BONSAI_HOME")
    if not home:
        sys.exit("BONSAI_HOME is not set; cannot start the server")
    home = Path(home).expanduser()
    jobs = B.jobs_dir()
    env = os.environ.copy()
    env.update(
        PORT=str(B.PORT),
        BONSAI_HOST="127.0.0.1",
        BONSAI_CTX=str(B.CTX),
        BONSAI_FAMILY=B.MODEL,
    )
    args = [
        str(home / "scripts" / "start_llama_server.sh"),
        "--alias", B.MODEL,
        "--reasoning-budget", B.REASONING_BUDGET,
    ]
    log = (jobs / "server.log").open("w")
    p = subprocess.Popen(
        args, cwd=home, env=env, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    B.write_json(jobs / "server.json", dict(pid=p.pid, started=time.time(), args=args))
    deadline = time.time() + LOAD_SECONDS
    while time.time() < deadline:
        if p.poll() is not None:
            print(f"server exited with code {p.returncode}; see {jobs / 'server.log'}")
            return 1
        if B.server_models() is not None:
            return check()
        time.sleep(5)
    print(f"server did not answer within {LOAD_SECONDS} s; see {jobs / 'server.log'}")
    return 1


def down():
    path = B.jobs_dir() / "server.json"
    record = B.read_json(path)
    if not record or not B.pid_alive(record["pid"]):
        print("no running server started by this skill; nothing stopped")
        return 0
    pgid = record["pid"]
    os.killpg(pgid, signal.SIGTERM)
    for _ in range(20):
        if not B.pid_alive(pgid):
            break
        time.sleep(0.5)
    else:
        os.killpg(pgid, signal.SIGKILL)
    path.unlink()
    print("stopped")
    return 0


if __name__ == "__main__":
    commands = dict(check=check, up=up, down=down)
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    sys.exit(commands[sys.argv[1]]())
