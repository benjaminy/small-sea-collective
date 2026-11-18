"""The demo scenario through the Hub: Alice moves an event, Bob adds a location, both sync.

Alice and Bob each have their own calendar repo and their own MinIO server.
Each publishes encrypted history through the Hub to their own bucket and
fetches the other's history through the Hub.

Team setup follows ssc-files/tests/test_hub_sync.py and
test_removed_teammate_history.py, with the same shortcuts: one in-process
Hub and one data directory for both teammates, sessions approved
automatically, Bob's sender key handed to Alice directly, and Bob's storage
announcement copied into Alice's team database by hand (#138).
Calendar commits are unsigned and fetched history is not verified.
"""

import pathlib
import sqlite3

import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as Provisioning
from cod_sync.protocol import CodSync
from cod_sync.repo import Repo
from cod_sync.store import PeerSmallSeaStore, SmallSeaStore
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager.manager import TeamManager, _CORE_APP
from test_support import accept_and_export, acceptance_record_from_courier

from tide_table.calendar_repo import CalendarRepo, peer_ref

APP = "TideTable"
TEAM = "ProjectX"
HUB_URL = "http://testserver"
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "move-vs-location"
EVENT = "event.ics"


def _fixture(name):
    return (FIXTURES / name).read_bytes()


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


def _publish_core(http, core_token, root, participant_hex, manager):
    """Publish a participant's team Core repo, so teammates can read it."""
    _setup_cloud(http, core_token)
    report = manager.reconcile_team_route(TEAM, app_name=_CORE_APP)
    assert report["route"] == "ready", report
    sync_dir = root / "Participants" / participant_hex / TEAM / "Sync"
    store = SmallSeaStore(core_token, base_url=HUB_URL, client=http)
    CodSync(Repo(sync_dir / ".git", sync_dir), store).publish()


def _setup_two_teammate_team(root, minio_server_gen):
    minio = {"Alice": minio_server_gen(), "Bob": minio_server_gen()}
    app.state.backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    http = TestClient(app)

    hexes = {name: Provisioning.create_new_participant(root, name) for name in ("Alice", "Bob")}
    managers = {name: TeamManager(root, h, _http_client=http) for name, h in hexes.items()}
    cloud = {}
    for name, h in hexes.items():
        Provisioning.register_app_for_participant(root, h, APP)
        _open_session(http, name, team="NoteToSelf", mode="passthrough")
        cloud[name] = Provisioning.add_cloud_storage(
            root, h, protocol="s3", url=minio[name]["endpoint"],
            access_key=minio[name]["access_key"], secret_key=minio[name]["secret_key"])

    def allocate_calendar_storage(name):
        token = _open_session(http, name)
        berth = _berth_id(http, token)
        Provisioning.add_berth_cloud_allocation_by_berth_id(
            root, hexes[name], berth, cloud[name], location=f"ss-{berth[:16]}")
        _setup_cloud(http, token)
        return token

    # Alice creates the team and invites Bob.
    alice_teammate_id = Provisioning.create_team(root, hexes["Alice"], TEAM)["teammate_id_hex"]
    Provisioning.activate_app_for_team(root, hexes["Alice"], TEAM, APP)
    tokens = {"Alice": allocate_calendar_storage("Alice")}
    assert managers["Alice"].reconcile_team_route(TEAM, app_name=APP)["route"] == "ready"
    alice_core = _open_session(http, "Alice", app_name=_CORE_APP)
    _publish_core(http, alice_core, root, hexes["Alice"], managers["Alice"])
    invitation = Provisioning.create_invitation(
        root, hexes["Alice"], TEAM, {"protocol": "s3", "url": minio["Alice"]["endpoint"]}, invitee_label="Bob")
    _publish_core(http, alice_core, root, hexes["Alice"], managers["Alice"])

    # Bob accepts; Alice completes the acceptance; Bob reads Alice's Core.
    acceptance = accept_and_export(managers["Bob"], invitation)
    bob_teammate_id = acceptance_record_from_courier(acceptance)["author_teammate_id"]
    tokens["Bob"] = allocate_calendar_storage("Bob")
    Provisioning.complete_invitation_acceptance(root, hexes["Alice"], TEAM, acceptance)
    _publish_core(http, alice_core, root, hexes["Alice"], managers["Alice"])
    managers["Bob"].fetch_teammate_core(TEAM, alice_teammate_id)
    managers["Bob"].integrate_core_sources(TEAM)
    assert managers["Bob"].reconcile_team_route(TEAM, app_name=APP)["route"] == "ready"

    # Alice needs Bob's sender key and the location of Bob's bucket.
    distribution = Provisioning.redistribute_sender_key(root, hexes["Bob"], TEAM)
    Provisioning.receive_sender_key_distribution(
        root, hexes["Alice"], TEAM, distribution["artifacts"][0]["distribution_payload"])
    bob_core = _open_session(http, "Bob", app_name=_CORE_APP)
    _publish_core(http, bob_core, root, hexes["Bob"], managers["Bob"])
    managers["Alice"].fetch_teammate_core(TEAM, bob_teammate_id)
    managers["Alice"].integrate_core_sources(TEAM)
    with sqlite3.connect(str(Provisioning._team_db_path(root, hexes["Bob"], TEAM))) as bob_db, \
            sqlite3.connect(str(Provisioning._team_db_path(root, hexes["Alice"], TEAM))) as alice_db:
        rows = bob_db.execute(
            "SELECT * FROM teammate_berth_storage_announcement WHERE teammate_id = ?",
            (bytes.fromhex(bob_teammate_id),)).fetchall()
        alice_db.executemany(
            "INSERT OR IGNORE INTO teammate_berth_storage_announcement VALUES (?,?,?,?,?,?,?,?,?)", rows)
    assert managers["Alice"].reconcile_team_route(TEAM, app_name=APP)["route"] == "ready"

    return http, tokens, {"Alice": alice_teammate_id, "Bob": bob_teammate_id}


def test_move_and_location_merge_through_the_hub(tmp_path, minio_server_gen):
    http, tokens, teammate_ids = _setup_two_teammate_team(tmp_path / "hub", minio_server_gen)
    cals = {name: CalendarRepo.create(tmp_path / name.lower(), name) for name in ("Alice", "Bob")}

    def publish(me):
        cals[me].publish(SmallSeaStore(tokens[me], base_url=HUB_URL, client=http))

    def sync(me, them):
        store = PeerSmallSeaStore(tokens[me], teammate_ids[them], base_url=HUB_URL, client=http)
        cals[me].fetch(store, teammate_ids[them])
        return cals[me].merge(peer_ref(teammate_ids[them]))

    # Alice creates the event; Bob syncs and has it too.
    cals["Alice"].save(EVENT, _fixture("base.ics"), "create")
    publish("Alice")
    sync("Bob", "Alice")
    assert cals["Bob"].read(EVENT) == _fixture("base.ics")

    # Concurrently: Alice moves it, Bob adds a location.
    cals["Alice"].save(EVENT, _fixture("apple-move.ics"), "move")
    cals["Bob"].save(EVENT, _fixture("thunderbird-location.ics"), "location")
    publish("Alice")
    publish("Bob")

    # Alice syncs first and merges; Bob then catches up to her merge.
    assert sync("Alice", "Bob") == [EVENT]
    publish("Alice")
    assert sync("Bob", "Alice") == []
    for cal in cals.values():
        text = cal.read(EVENT)
        assert b"DTSTART;TZID=America/Denver:20261119T151500" in text
        assert b"LOCATION:over theere" in text
        assert b"SEQUENCE:2" in text
    assert cals["Alice"].repo.head() == cals["Bob"].repo.head()
