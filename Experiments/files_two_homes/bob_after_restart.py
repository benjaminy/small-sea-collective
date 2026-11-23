"""Bob's post-restart pull, run as a fresh Files process.

Invoked by `test_two_homes.py` with no injected HTTP client and no fresh
login: everything comes from the config file and Files state already on
disk, and the Hub is reached over real loopback HTTP.
"""

import functools
import anyio
import hashlib
import json
import os
import pathlib
import sys

from ssc_files import files, sync


async def _session_info(team, hub_port):
    async with sync.hub_http(hub_port) as http:
        return await sync.get_team_session(team, http).session_info()


def main():
    request = json.loads(sys.argv[1])
    team = request["team"]
    participant = request["participant"]

    info = anyio.run(_session_info, team, request["hub_port"])
    context = files.materialization_context_from_session_info(info)

    anyio.run(functools.partial(sync.pull_via_hub, request["files_root"], participant, team, request["niche"], request["from_teammate_id"], hub_port=request["hub_port"]))
    content = pathlib.Path(request["checkout"], request["path"]).read_bytes()

    json.dump({
        "pid": os.getpid(),
        "config_path": str(sync.config_path()),
        "session_team_name": info["team_name"],
        "session_participant_hex": info["participant_hex"],
        "session_app_name": info["app_name"],
        "session_berth_id": info["berth_id"],
        "context_team_id": context.team_id,
        "content": content.decode(),
        "sha256": hashlib.sha256(content).hexdigest(),
    }, sys.stdout, sort_keys=True)


if __name__ == "__main__":
    main()
