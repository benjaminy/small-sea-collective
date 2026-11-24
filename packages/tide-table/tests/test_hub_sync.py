"""The demo scenario through two Hubs: Alice moves an event, Bob adds a location, both sync.

Alice and Bob each have their own data directory, their own Hub process, their
own MinIO server, and their own calendar repo. Each publishes encrypted history
through their own Hub to their own bucket and fetches the other's history
through their own Hub. The two Hubs never talk to each other directly.

Team setup follows ssc-files/tests/test_hub_sync.py and
test_removed_teammate_history.py. Remaining shortcuts: sessions are approved
automatically, Manager steps run as direct calls against each teammate's
data directory, Bob's sender key is handed to Alice directly, and Bob's
storage announcement is copied into Alice's team database by hand (#138).
Calendar commits are signed through each teammate's Hub, and each Hub judges
the history its teammate fetches.
"""

import anyio
import contextlib
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest
import small_sea_manager.provisioning as Provisioning
from cod_sync.protocol import CodSync
from cod_sync.async_repo import Repo
from cod_sync.store import PeerSmallSeaStore
from cod_sync.store import SmallSeaStore
from small_sea_manager.manager import TeamManager, _CORE_APP
from test_support import _shut_down, accept_and_export, acceptance_record_from_courier, reserve_ports

from tide_table.calendar_repo import CalendarRepo, peer_ref
from tide_table.hub_signer import HubSigner, UnauthorizedHistory

APP = "TideTable"
TEAM = "ProjectX"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "move-vs-location"
EVENT = "event.ics"


def _fixture(name):
    return (FIXTURES / name).read_bytes()


def start_hub(root_dir, procs, log_path=None):
    """Run a Hub process over root_dir; return its base URL."""
    port = reserve_ports([None])[0]
    env = dict(os.environ, SMALL_SEA_ROOT_DIR=str(root_dir), SMALL_SEA_AUTO_APPROVE_SESSIONS="true",
               SMALL_SEA_LOG_LEVEL="WARNING")
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "small_sea_hub.server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, stdout=log, stderr=subprocess.STDOUT)
    procs.append(proc)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"Hub exited early (code {proc.returncode})")
        try:
            httpx.get(url + "/", timeout=0.5)
            return port, url
        except httpx.TransportError:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Hub at {url} did not start")
            time.sleep(0.1)


@pytest.fixture
def hub_gen():
    procs = []
    yield lambda root_dir: start_hub(root_dir, procs)
    for proc in procs:
        _shut_down(proc)


def _open_session(http, nickname, app_name=APP, team=TEAM, mode="encrypted"):
    resp = http.post("/sessions/request", json={
        "participant": nickname, "app": app_name, "team": team,
        "client": "Tide Table tests", "mode": mode,
    })
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]
    resp = http.post("/sessions/confirm", json={"pending_id": result["pending_id"], "pin": result["pin"]})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _setup_cloud(http, token):
    resp = http.post("/cloud/setup", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


def _berth_id(http, token):
    return http.get("/session/info", headers={"Authorization": f"Bearer {token}"}).json()["berth_id"]


async def _publish_core(http, core_token, root, participant_hex, manager):
    """Publish a participant's team Core repo, so teammates can read it."""
    _setup_cloud(http, core_token)
    report = await manager.reconcile_team_route(TEAM, app_name=_CORE_APP)
    assert report["route"] == "ready", report
    sync_dir = root / "Participants" / participant_hex / TEAM / "Sync"
    async with httpx.AsyncClient(base_url=str(http.base_url), timeout=http.timeout) as client:
        store = SmallSeaStore(core_token, client)
        await CodSync(Repo(sync_dir / ".git", sync_dir), store).publish()


async def _setup_two_teammate_team(root, minio_server_gen, hub_gen):
    """Return {name: {"hub": url, "token": calendar session, "teammate_id": hex}}."""
    names = ("Alice", "Bob")
    roots = {name: root / name.lower() for name in names}
    minio, hubs, http, hexes, managers, cloud = {}, {}, {}, {}, {}, {}
    for name in names:
        roots[name].mkdir(parents=True)
        minio[name] = minio_server_gen()
        # The participant must exist before the Hub starts reading the directory.
        hexes[name] = Provisioning.create_new_participant(roots[name], name)
        port, hubs[name] = hub_gen(roots[name])
        http[name] = httpx.Client(base_url=hubs[name], timeout=60)
        managers[name] = TeamManager(roots[name], hexes[name], hub_port=port)
        Provisioning.register_app_for_participant(roots[name], hexes[name], APP)
        _open_session(http[name], name, team="NoteToSelf", mode="passthrough")
        cloud[name] = Provisioning.add_cloud_storage(
            roots[name], hexes[name], protocol="s3", url=minio[name]["endpoint"],
            access_key=minio[name]["access_key"], secret_key=minio[name]["secret_key"])

    def allocate_calendar_storage(name):
        token = _open_session(http[name], name)
        berth = _berth_id(http[name], token)
        Provisioning.add_berth_cloud_allocation_by_berth_id(
            roots[name], hexes[name], berth, cloud[name], location=f"ss-{berth[:16]}")
        _setup_cloud(http[name], token)
        # Creates this berth's signing key and records its delegation in Core,
        # so the Core publishes below carry it to the teammate.
        resp = http[name].get("/session/signing_key", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        return token

    # Alice creates the team and invites Bob.
    alice_teammate_id = Provisioning.create_team(roots["Alice"], hexes["Alice"], TEAM)["teammate_id_hex"]
    Provisioning.activate_app_for_team(roots["Alice"], hexes["Alice"], TEAM, APP)
    tokens = {"Alice": allocate_calendar_storage("Alice")}
    assert (await managers["Alice"].reconcile_team_route(TEAM, app_name=APP))["route"] == "ready"
    alice_core = _open_session(http["Alice"], "Alice", app_name=_CORE_APP)
    await _publish_core(http["Alice"], alice_core, roots["Alice"], hexes["Alice"], managers["Alice"])
    invitation = Provisioning.create_invitation(
        roots["Alice"], hexes["Alice"], TEAM, {"protocol": "s3", "url": minio["Alice"]["endpoint"]},
        invitee_label="Bob")
    await _publish_core(http["Alice"], alice_core, roots["Alice"], hexes["Alice"], managers["Alice"])

    # Bob accepts; Alice completes the acceptance; Bob reads Alice's Core.
    # The helper starts its own event loop, so it cannot run on this one.
    acceptance = await anyio.to_thread.run_sync(accept_and_export, managers["Bob"], invitation)
    bob_teammate_id = acceptance_record_from_courier(acceptance)["author_teammate_id"]
    tokens["Bob"] = allocate_calendar_storage("Bob")
    Provisioning.complete_invitation_acceptance(roots["Alice"], hexes["Alice"], TEAM, acceptance)
    await _publish_core(http["Alice"], alice_core, roots["Alice"], hexes["Alice"], managers["Alice"])
    await managers["Bob"].fetch_teammate_core(TEAM, alice_teammate_id)
    managers["Bob"].integrate_core_sources(TEAM)
    assert (await managers["Bob"].reconcile_team_route(TEAM, app_name=APP))["route"] == "ready"

    # Alice needs Bob's sender key and the location of Bob's bucket.
    distribution = Provisioning.redistribute_sender_key(roots["Bob"], hexes["Bob"], TEAM)
    Provisioning.receive_sender_key_distribution(
        roots["Alice"], hexes["Alice"], TEAM, distribution["artifacts"][0]["distribution_payload"])
    bob_core = _open_session(http["Bob"], "Bob", app_name=_CORE_APP)
    await _publish_core(http["Bob"], bob_core, roots["Bob"], hexes["Bob"], managers["Bob"])
    await managers["Alice"].fetch_teammate_core(TEAM, bob_teammate_id)
    managers["Alice"].integrate_core_sources(TEAM)
    with sqlite3.connect(str(Provisioning._team_db_path(roots["Bob"], hexes["Bob"], TEAM))) as bob_db, \
            sqlite3.connect(str(Provisioning._team_db_path(roots["Alice"], hexes["Alice"], TEAM))) as alice_db:
        rows = bob_db.execute(
            "SELECT * FROM teammate_berth_storage_announcement WHERE teammate_id = ?",
            (bytes.fromhex(bob_teammate_id),)).fetchall()
        alice_db.executemany(
            "INSERT OR IGNORE INTO teammate_berth_storage_announcement VALUES (?,?,?,?,?,?,?,?,?)", rows)
    assert (await managers["Alice"].reconcile_team_route(TEAM, app_name=APP))["route"] == "ready"

    teammate_ids = {"Alice": alice_teammate_id, "Bob": bob_teammate_id}
    return {name: {"hub": hubs[name], "token": tokens[name], "teammate_id": teammate_ids[name]}
            for name in names}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_move_and_location_merge_through_two_hubs(tmp_path, minio_server_gen, hub_gen):
    team = await _setup_two_teammate_team(tmp_path / "devices", minio_server_gen, hub_gen)
    async with contextlib.AsyncExitStack() as stack:
        http = {name: await stack.enter_async_context(
                    httpx.AsyncClient(base_url=team[name]["hub"], timeout=60)) for name in team}
        await _move_and_location(tmp_path, team, http)


async def _move_and_location(tmp_path, team, http):
    cals = {}
    for name in team:
        signer = await HubSigner.open(team[name]["hub"], team[name]["token"], http[name])
        cals[name] = await CalendarRepo.create(tmp_path / name.lower() / "calendar", name, signer)

    async def publish(me):
        await cals[me].publish(SmallSeaStore(team[me]["token"], http[me]))

    async def sync(me, them):
        them_id = team[them]["teammate_id"]
        store = PeerSmallSeaStore(team[me]["token"], them_id, http[me])
        await cals[me].fetch(store, them_id)
        return await cals[me].merge(peer_ref(them_id))

    # Alice creates the event; Bob syncs and has it too.
    await cals["Alice"].save(EVENT, _fixture("base.ics"), "create")
    await publish("Alice")
    await sync("Bob", "Alice")
    assert cals["Bob"].read(EVENT) == _fixture("base.ics")

    # Concurrently: Alice moves it, Bob adds a location.
    await cals["Alice"].save(EVENT, _fixture("apple-move.ics"), "move")
    await cals["Bob"].save(EVENT, _fixture("thunderbird-location.ics"), "location")
    await publish("Alice")
    await publish("Bob")

    # Alice syncs first and merges; Bob then catches up to her merge.
    assert await sync("Alice", "Bob") == [EVENT]
    await publish("Alice")
    assert await sync("Bob", "Alice") == []
    for cal in cals.values():
        text = cal.read(EVENT)
        assert b"DTSTART;TZID=America/Denver:20261119T151500" in text
        assert b"LOCATION:over theere" in text
        assert b"SEQUENCE:2" in text
    assert await cals["Alice"].repo.head() == await cals["Bob"].repo.head()
    for commit in (await cals["Alice"].repo._run(["rev-list", "HEAD"])).stdout.split():
        body = (await cals["Alice"].repo._run(["cat-file", "commit", commit])).stdout
        assert "\ngpgsig " in body and "\nSmall-Sea-Work-Context: " in body, body

    # A commit Bob's Hub did not sign is refused when Alice fetches it.
    unsigned = CalendarRepo(cals["Bob"].path)
    await unsigned.save(EVENT, _fixture("base.ics"), "unsigned")
    await unsigned.publish(SmallSeaStore(team["Bob"]["token"], http["Bob"]))
    head = await cals["Alice"].repo.head()
    with pytest.raises(UnauthorizedHistory):
        await sync("Alice", "Bob")
    assert await cals["Alice"].repo.head() == head
