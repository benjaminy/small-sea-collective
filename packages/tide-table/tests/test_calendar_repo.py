"""Micro tests for two teammates' calendar repos syncing through folder stores.

Each teammate publishes to their own folder and fetches from the other's,
the same shape as Hub sync without the Hub. `test_hub_sync.py` runs the
demo scenario through the Hub.
"""

import pathlib

import pytest
from cod_sync.aio.store import LocalFolderStore
from cod_sync.repo import RepoError

from tide_table.calendar_repo import CalendarRepo, EventConflict, peer_ref

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "move-vs-location"
EVENT = "event.ics"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _fixture(name):
    return (FIXTURES / name).read_bytes()


@pytest.fixture
async def team(tmp_path):
    alice = await CalendarRepo.create(tmp_path / "alice", "Alice")
    bob = await CalendarRepo.create(tmp_path / "bob", "Bob")
    stores = {}
    for name in ("alice", "bob"):
        (tmp_path / f"{name}-store").mkdir()
        stores[name] = LocalFolderStore(tmp_path / f"{name}-store")

    async def sync(me, them):
        repo = {"alice": alice, "bob": bob}[me]
        await repo.fetch(stores[them], them)
        return await repo.merge(peer_ref(them))

    async def publish(me):
        await {"alice": alice, "bob": bob}[me].publish(stores[me])

    await alice.save(EVENT, _fixture("base.ics"), "create")
    await publish("alice")
    await sync("bob", "alice")
    return alice, bob, publish, sync


def _assert_merged(repo):
    text = repo.read(EVENT)
    assert b"DTSTART;TZID=America/Denver:20261119T151500" in text
    assert b"LOCATION:over theere" in text
    assert b"SEQUENCE:2" in text


async def test_move_and_location_merge_then_teammate_catches_up(team):
    alice, bob, publish, sync = team
    await alice.save(EVENT, _fixture("apple-move.ics"), "move")
    await bob.save(EVENT, _fixture("thunderbird-location.ics"), "location")
    await publish("bob")

    assert await sync("alice", "bob") == [EVENT]
    await publish("alice")
    assert await sync("bob", "alice") == []  # fast-forward to Alice's merge commit
    _assert_merged(alice)
    assert await alice.repo.head() == await bob.repo.head()


async def test_both_teammates_merge_concurrently_then_converge(team):
    alice, bob, publish, sync = team
    await alice.save(EVENT, _fixture("apple-move.ics"), "move")
    await bob.save(EVENT, _fixture("thunderbird-location.ics"), "location")
    await publish("alice")
    await publish("bob")

    assert await sync("alice", "bob") == [EVENT]
    assert await sync("bob", "alice") == [EVENT]
    _assert_merged(alice)
    _assert_merged(bob)

    # Each merge took the calendar header and zone from its own side, so the
    # two merge commits differ. One more round settles on a single history.
    await publish("alice")
    await publish("bob")
    await sync("alice", "bob")
    await publish("alice")
    await sync("bob", "alice")
    _assert_merged(alice)
    assert await alice.repo.head() == await bob.repo.head()


async def test_events_created_before_first_sync_merge(tmp_path):
    stores = {}
    cals = {}
    for name in ("alice", "bob"):
        (tmp_path / f"{name}-store").mkdir()
        stores[name] = LocalFolderStore(tmp_path / f"{name}-store")
        cals[name] = await CalendarRepo.create(tmp_path / name, name)
    await cals["alice"].save("a.ics", _fixture("base.ics"), "alice's event")
    await cals["bob"].save("b.ics", _fixture("base.ics").replace(b"UID:", b"UID:b-"), "bob's event")
    await cals["bob"].publish(stores["bob"])

    await cals["alice"].fetch(stores["bob"], "bob")
    await cals["alice"].merge(peer_ref("bob"))
    assert sorted(p.name for p in (tmp_path / "alice").glob("*.ics")) == ["a.ics", "b.ics"]


async def test_deleted_event_reaches_teammate(team):
    alice, bob, publish, sync = team
    await alice.delete(EVENT, "delete")
    await publish("alice")
    await sync("bob", "alice")
    assert not (bob.path / EVENT).exists()


@pytest.mark.xfail(strict=True, raises=EventConflict,
                   reason="a delete after crossed merges reaches merge_event as a delete/modify conflict")
async def test_delete_after_both_teammates_merged_the_same_edits(team):
    alice, bob, publish, sync = team
    await alice.save(EVENT, _fixture("apple-move.ics"), "move")
    await bob.save(EVENT, _fixture("thunderbird-location.ics"), "location")
    await publish("alice")
    await publish("bob")
    await sync("alice", "bob")
    await sync("bob", "alice")

    # Bob deletes the event before another round settles the two merge
    # commits on one history. Both merges hold the same edits, so the delete
    # throws away nothing Bob did not see.
    await bob.delete(EVENT, "delete")
    await publish("bob")
    await sync("alice", "bob")
    await publish("alice")
    await sync("bob", "alice")

    assert not (alice.path / EVENT).exists()
    assert not (bob.path / EVENT).exists()
    assert await alice.repo.head() == await bob.repo.head()


async def test_conflicting_edits_leave_both_histories_untouched(team):
    alice, bob, publish, sync = team
    loc = _fixture("thunderbird-location.ics")
    await alice.save(EVENT, loc.replace(b"over theere", b"kitchen"), "kitchen")
    await bob.save(EVENT, loc.replace(b"over theere", b"garden"), "garden")
    await publish("bob")
    head = await alice.repo.head()

    with pytest.raises(EventConflict) as exc:
        await sync("alice", "bob")
    assert exc.value.paths == [EVENT]
    assert await alice.repo.head() == head
    assert b"LOCATION:kitchen" in alice.read(EVENT)
    assert await alice.repo.status() == []
    assert await alice.repo.resolve_ref(peer_ref("bob")) == await bob.repo.head()


async def test_merge_reports_git_failure_without_conflicts(team):
    alice, bob, publish, sync = team
    await alice.save("alice.ics", _fixture("base.ics"), "local edit")
    await bob.save(EVENT, _fixture("apple-move.ics"), "move")
    await publish("bob")
    head = await alice.repo.head()
    index_lock = alice.git_dir / "index.lock"
    index_lock.touch()

    with pytest.raises(RepoError, match=r"index\.lock"):
        await sync("alice", "bob")
    assert await alice.repo.head() == head
    assert await alice.repo.resolve_ref(peer_ref("bob")) == await bob.repo.head()

    index_lock.unlink()
    assert await sync("alice", "bob") == []
    assert alice.read(EVENT) == _fixture("apple-move.ics")
