"""Micro tests for two teammates' calendar repos syncing through folder stores.

Each teammate publishes to their own folder and fetches from the other's,
the same shape as Hub sync without the Hub. `test_hub_sync.py` runs the
demo scenario through the Hub.
"""

import pathlib

import pytest
from cod_sync.store import LocalFolderStore

from tide_table.calendar_repo import CalendarRepo, EventConflict, peer_ref

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "move-vs-location"
EVENT = "event.ics"


def _fixture(name):
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def team(tmp_path):
    alice = CalendarRepo.create(tmp_path / "alice", "Alice")
    bob = CalendarRepo.create(tmp_path / "bob", "Bob")
    stores = {}
    for name in ("alice", "bob"):
        (tmp_path / f"{name}-store").mkdir()
        stores[name] = LocalFolderStore(tmp_path / f"{name}-store")

    def sync(me, them):
        repo = {"alice": alice, "bob": bob}[me]
        repo.fetch(stores[them], them)
        return repo.merge(peer_ref(them))

    def publish(me):
        {"alice": alice, "bob": bob}[me].publish(stores[me])

    alice.save(EVENT, _fixture("base.ics"), "create")
    publish("alice")
    sync("bob", "alice")
    return alice, bob, publish, sync


def _assert_merged(repo):
    text = repo.read(EVENT)
    assert b"DTSTART;TZID=America/Denver:20261119T151500" in text
    assert b"LOCATION:over theere" in text
    assert b"SEQUENCE:2" in text


def test_move_and_location_merge_then_teammate_catches_up(team):
    alice, bob, publish, sync = team
    alice.save(EVENT, _fixture("apple-move.ics"), "move")
    bob.save(EVENT, _fixture("thunderbird-location.ics"), "location")
    publish("bob")

    assert sync("alice", "bob") == [EVENT]
    publish("alice")
    assert sync("bob", "alice") == []  # fast-forward to Alice's merge commit
    _assert_merged(alice)
    assert alice.repo.head() == bob.repo.head()


def test_both_teammates_merge_concurrently_then_converge(team):
    alice, bob, publish, sync = team
    alice.save(EVENT, _fixture("apple-move.ics"), "move")
    bob.save(EVENT, _fixture("thunderbird-location.ics"), "location")
    publish("alice")
    publish("bob")

    assert sync("alice", "bob") == [EVENT]
    assert sync("bob", "alice") == [EVENT]
    _assert_merged(alice)
    _assert_merged(bob)

    # Each merge took the calendar header and zone from its own side, so the
    # two merge commits differ. One more round settles on a single history.
    publish("alice")
    publish("bob")
    sync("alice", "bob")
    publish("alice")
    sync("bob", "alice")
    _assert_merged(alice)
    assert alice.repo.head() == bob.repo.head()


def test_events_created_before_first_sync_merge(tmp_path):
    stores = {}
    cals = {}
    for name in ("alice", "bob"):
        (tmp_path / f"{name}-store").mkdir()
        stores[name] = LocalFolderStore(tmp_path / f"{name}-store")
        cals[name] = CalendarRepo.create(tmp_path / name, name)
    cals["alice"].save("a.ics", _fixture("base.ics"), "alice's event")
    cals["bob"].save("b.ics", _fixture("base.ics").replace(b"UID:", b"UID:b-"), "bob's event")
    cals["bob"].publish(stores["bob"])

    cals["alice"].fetch(stores["bob"], "bob")
    cals["alice"].merge(peer_ref("bob"))
    assert sorted(p.name for p in (tmp_path / "alice").glob("*.ics")) == ["a.ics", "b.ics"]


def test_deleted_event_reaches_teammate(team):
    alice, bob, publish, sync = team
    alice.delete(EVENT, "delete")
    publish("alice")
    sync("bob", "alice")
    assert not (bob.path / EVENT).exists()


def test_conflicting_edits_leave_both_histories_untouched(team):
    alice, bob, publish, sync = team
    loc = _fixture("thunderbird-location.ics")
    alice.save(EVENT, loc.replace(b"over theere", b"kitchen"), "kitchen")
    bob.save(EVENT, loc.replace(b"over theere", b"garden"), "garden")
    publish("bob")
    head = alice.repo.head()

    with pytest.raises(EventConflict) as exc:
        sync("alice", "bob")
    assert exc.value.paths == [EVENT]
    assert alice.repo.head() == head
    assert b"LOCATION:kitchen" in alice.read(EVENT)
    assert alice.repo.status() == []
    assert alice.repo.resolve_ref(peer_ref("bob")) == bob.repo.head()
