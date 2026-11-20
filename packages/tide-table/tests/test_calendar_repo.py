"""Micro tests for two teammates' calendar repos syncing through folder stores.

Each teammate publishes to their own folder and fetches from the other's,
the same shape as Hub sync without the Hub. `test_hub_sync.py` runs the
demo scenario through the Hub.
"""

import pathlib

import pytest
from cod_sync.aio.store import LocalFolderStore
from cod_sync.repo import RepoError

from tide_table.calendar_repo import CalendarRepo, EventConflict, event_uid, peer_ref

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
    assert cals["alice"].names() == ["a.ics", "b.ics"]


async def test_deleted_event_reaches_teammate(team):
    alice, bob, publish, sync = team
    await alice.delete(EVENT, "delete")
    await publish("alice")
    await sync("bob", "alice")
    assert bob.read(EVENT) is None


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

    assert alice.read(EVENT) is None
    assert bob.read(EVENT) is None
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
    assert exc.value.conflicts == [f"update/update {EVENT}"]
    assert await alice.repo.head() == head
    assert b"LOCATION:kitchen" in alice.read(EVENT)
    assert await alice.repo.status() == []
    assert await alice.repo.resolve_ref(peer_ref("bob")) == await bob.repo.head()


async def test_merge_reports_git_failure_without_conflicts(team):
    alice, bob, publish, sync = team
    await alice.save("alice.ics", _fixture("base.ics").replace(b"UID:", b"UID:a-"), "local edit")
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


async def test_edit_against_delete_conflicts(team):
    alice, bob, publish, sync = team
    await alice.save(EVENT, _fixture("apple-move.ics"), "move")
    await bob.delete(EVENT, "delete")
    await publish("bob")

    with pytest.raises(EventConflict) as exc:
        await sync("alice", "bob")
    assert exc.value.conflicts == [f"delete/modify {EVENT}"]
    assert alice.read(EVENT) == _fixture("apple-move.ics")


async def test_two_events_with_one_uid_conflict(team):
    # Say both teammates added the same emailed invitation, under different names.
    alice, bob, publish, sync = team
    invitation = _fixture("base.ics").replace(b"UID:", b"UID:invite-")
    await alice.save("alices-copy.ics", invitation, "add invitation")
    await bob.save("bobs-copy.ics", invitation, "add invitation")
    await publish("bob")

    with pytest.raises(EventConflict, match="two events with uid invite-"):
        await sync("alice", "bob")
    assert alice.names() == ["alices-copy.ics", EVENT]


async def test_each_keeping_their_own_copy_is_still_reported(team):
    """Each teammate resolves a duplicate UID by keeping their own copy; their next sync must not delete both."""
    alice, bob, publish, sync = team
    invitation = _fixture("base.ics").replace(b"UID:", b"UID:invite-")
    await alice.save("alices-copy.ics", invitation, "add invitation")
    await bob.save("bobs-copy.ics", invitation, "add invitation")
    await publish("alice")
    await publish("bob")
    for me, them in ((alice, "bob"), (bob, "alice")):
        with pytest.raises(EventConflict):
            await sync({alice: "alice", bob: "bob"}[me], them)
        await me._git("merge", "-q", "-s", "ours", "--no-edit", peer_ref(them))

    await publish("bob")
    with pytest.raises(EventConflict) as exc:
        await sync("alice", "bob")
    assert sorted(exc.value.conflicts) == ["delete/modify alices-copy.ics", "modify/delete bobs-copy.ics"]
    assert alice.names() == ["alices-copy.ics", EVENT]

    # Bob takes Alice's copy instead of his own.
    await bob._git("checkout", peer_ref("alice"), "--", "calendar.sqlite")
    await bob.commit("use Alice's copy")
    await publish("bob")
    await sync("alice", "bob")
    assert alice.names() == ["alices-copy.ics", EVENT]


def test_a_uid_containing_a_colon_is_kept_whole():
    assert event_uid(b"BEGIN:VEVENT\r\nUID:mailto:alice\r\nEND:VEVENT\r\n") == "mailto:alice"
    assert event_uid(b'BEGIN:VEVENT\r\nUID;X-NOTE="a:b":alice\r\nEND:VEVENT\r\n') == "alice"


async def test_a_disagreement_both_sides_kept_is_still_reported(team):
    """Each teammate resolves a conflict by keeping their own room; their next sync must not hide it."""
    alice, bob, publish, sync = team
    loc = _fixture("thunderbird-location.ics")
    await alice.save(EVENT, loc.replace(b"over theere", b"Room A"), "Room A")
    await bob.save(EVENT, loc.replace(b"over theere", b"Room B"), "Room B")
    await publish("alice")
    await publish("bob")
    for me, them in ((alice, "bob"), (bob, "alice")):
        with pytest.raises(EventConflict):
            await sync({alice: "alice", bob: "bob"}[me], them)
        await me._git("merge", "-q", "-s", "ours", "--no-edit", peer_ref(them))

    await publish("bob")
    # The crossed merges give git two candidate bases, which conflict with
    # each other; the driver records the event as having no agreed base.
    with pytest.raises(EventConflict):
        await sync("alice", "bob")

    # Bob comes round to Room A; his client stamps the event afresh.
    await bob.save(EVENT, loc.replace(b"over theere", b"Room A").replace(b"SEQUENCE:1", b"SEQUENCE:3"), "Room A")
    await publish("bob")
    await sync("alice", "bob")
    assert b"LOCATION:Room A" in alice.read(EVENT)


def _ids(exc):
    return [row["id"] for c in exc.value.details for row in c["rows"]]


async def test_choosing_theirs_settles_an_edit_conflict_for_both(team):
    alice, bob, publish, sync = team
    loc = _fixture("thunderbird-location.ics")
    await alice.save(EVENT, loc.replace(b"over theere", b"kitchen"), "kitchen")
    await bob.save(EVENT, loc.replace(b"over theere", b"garden"), "garden")
    await publish("bob")
    with pytest.raises(EventConflict) as exc:
        await sync("alice", "bob")
    (row,) = exc.value.details[0]["rows"]
    assert (row["name"], "kitchen" in row["ours"], "garden" in row["theirs"]) == (EVENT, True, True)

    await alice.merge(peer_ref("bob"), {row["id"]: "theirs"})
    assert b"LOCATION:garden" in alice.read(EVENT)
    await publish("alice")
    await sync("bob", "alice")
    assert await alice.repo.head() == await bob.repo.head()


async def test_choosing_ours_keeps_an_event_a_teammate_deleted(team):
    alice, bob, publish, sync = team
    await alice.save(EVENT, _fixture("apple-move.ics"), "move")
    await bob.delete(EVENT, "delete")
    await publish("bob")
    with pytest.raises(EventConflict) as exc:
        await sync("alice", "bob")
    assert exc.value.details[0]["rows"][0]["theirs"] is None

    await alice.merge(peer_ref("bob"), {i: "ours" for i in _ids(exc)})
    await publish("alice")
    await sync("bob", "alice")
    assert bob.read(EVENT) == _fixture("apple-move.ics")


async def test_choosing_theirs_settles_a_duplicate_uid(team):
    alice, bob, publish, sync = team
    invitation = _fixture("base.ics").replace(b"UID:", b"UID:invite-")
    await alice.save("alices-copy.ics", invitation, "add invitation")
    await bob.save("bobs-copy.ics", invitation, "add invitation")
    await publish("bob")
    with pytest.raises(EventConflict) as exc:
        await sync("alice", "bob")

    await alice.merge(peer_ref("bob"), {i: "theirs" for i in _ids(exc)})
    assert alice.names() == ["bobs-copy.ics", EVENT]


async def test_a_choice_for_only_some_events_settles_nothing(team):
    alice, bob, publish, sync = team
    loc = _fixture("thunderbird-location.ics")
    for name in ("one.ics", "two.ics"):
        await alice.save(name, loc.replace(b"UID:", f"UID:{name}-".encode()), f"add {name}")
    await publish("alice")
    await sync("bob", "alice")
    for name in ("one.ics", "two.ics"):
        await alice.save(name, alice.read(name).replace(b"over theere", b"kitchen"), "kitchen")
        await bob.save(name, bob.read(name).replace(b"over theere", b"garden"), "garden")
    await publish("bob")
    with pytest.raises(EventConflict) as exc:
        await sync("alice", "bob")
    first = exc.value.details[0]["rows"][0]["id"]

    with pytest.raises(EventConflict) as exc:
        await alice.merge(peer_ref("bob"), {first: "theirs"})
    assert len(exc.value.details) == 1
    assert b"kitchen" in alice.read("one.ics") and b"kitchen" in alice.read("two.ics")
