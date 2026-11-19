import asyncio
import pathlib
import threading
import time

import httpx
import pytest
from cod_sync.aio.store import LocalFolderStore
from cod_sync.store import StoreTransportError

import tide_table.sync_agent as sync_agent
from tide_table.sync_agent import Parked, SyncAgent, SyncFailed

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def spikes(monkeypatch):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "spikes"))


class _Agent:
    def __init__(self):
        self.publish_requests = 0

    def request_publish(self):
        self.publish_requests += 1


async def test_merge_records_changes_before_publishing(tmp_path, spikes):
    """A merge's changes reach CalDAV clients whether or not the publish that follows succeeds."""
    import two_teammates_demo as demo
    from caldav_spike import Store, etag_of

    stores = {name: Store(tmp_path / name, author=name) for name in ("Alice", "Bob")}
    cloud = {}
    for name in stores:
        (tmp_path / f"{name}-cloud").mkdir()
        cloud[name] = LocalFolderStore(tmp_path / f"{name}-cloud")

    alice, bob = stores["Alice"], stores["Bob"]
    event = "event.ics"
    alice.put(event, b"original")
    await alice.cal.publish(cloud["Alice"])
    await bob.cal.fetch(cloud["Alice"], "Alice")
    await bob.cal.merge(demo.peer_ref("Alice"))
    bob.put(event, b"updated")
    await bob.cal.publish(cloud["Bob"])

    token = alice.token
    head = await alice.cal.fetch(cloud["Bob"], "Bob")
    agent = _Agent()
    await demo.handle_event("Alice", alice, agent, {}, Parked("Bob", head))

    assert agent.publish_requests == 1
    assert alice.get(event) == b"updated"
    assert alice.changes_since(token) == ([event], [])
    assert alice.version(etag_of(b"updated")) == b"updated"
    assert Store(tmp_path / "Alice").changes_since(token) == ([event], [])


async def test_stopping_caldav_lets_a_write_in_progress_finish(tmp_path, spikes, monkeypatch):
    import two_teammates_demo as demo
    from caldav_spike import make_server

    server = make_server(0, "alice", "tide", str(tmp_path / "alice"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    store = server.store
    committing, release = threading.Event(), threading.Event()
    real_save = store.cal.save

    def held_save(*args):
        committing.set()  # where a real save would be waiting on the Hub to sign
        release.wait()
        return real_save(*args)

    monkeypatch.setattr(store.cal, "save", held_save)
    url = f"http://127.0.0.1:{server.server_address[1]}/dav/calendars/alice/team/event.ics"
    put = asyncio.create_task(asyncio.to_thread(httpx.put, url, content=b"body", auth=("alice", "tide")))
    await asyncio.to_thread(committing.wait, 5)

    stopping = asyncio.create_task(demo.stop_caldav([server]))
    await asyncio.sleep(0.2)
    assert not stopping.done()
    release.set()
    await asyncio.wait_for(stopping, 5)

    assert store.get("event.ics") == b"body"
    assert store.cal.repo.has_commits()
    assert (await put).status_code == 201
    server.server_close()


class _Worker:
    """Stands in for a SyncAgent: an event queue, and a record of when merges asked to publish."""
    def __init__(self):
        self.events = asyncio.Queue()
        self.merged = asyncio.Event()
        self.merged_at = None

    def request_publish(self):
        self.merged_at = time.monotonic()
        self.merged.set()


async def test_a_held_write_stalls_only_its_own_teammate(tmp_path, spikes, monkeypatch):
    """While Alice's write waits on signing, Bob still merges; Alice merges after her write."""
    import two_teammates_demo as demo
    from caldav_spike import Store, make_server

    server = make_server(0, "alice", "tide", str(tmp_path / "alice"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    alice, bob = server.store, Store(tmp_path / "bob", author="Bob")
    clouds = {}
    for name in ("Alice", "Bob"):
        (tmp_path / f"{name}-cloud").mkdir()
        clouds[name] = LocalFolderStore(tmp_path / f"{name}-cloud")
    alice.put("a.ics", b"from alice")
    await alice.cal.publish(clouds["Alice"])
    bob_parked = await bob.cal.fetch(clouds["Alice"], "Alice")
    bob.put("b.ics", b"from bob")
    await bob.cal.publish(clouds["Bob"])
    alice_parked = await alice.cal.fetch(clouds["Bob"], "Bob")

    committing, release = threading.Event(), threading.Event()
    real_save = alice.cal.save

    def held_save(*args):
        committing.set()  # where a real save would be waiting on the Hub to sign
        release.wait()
        return real_save(*args)

    monkeypatch.setattr(alice.cal, "save", held_save)
    url = f"http://127.0.0.1:{server.server_address[1]}/dav/calendars/alice/team/a2.ics"
    put = asyncio.create_task(asyncio.to_thread(httpx.put, url, content=b"held", auth=("alice", "tide")))
    await asyncio.to_thread(committing.wait, 5)

    workers = {"Alice": _Worker(), "Bob": _Worker()}
    tasks = [asyncio.create_task(demo.process_events(name, store, workers[name], {}))
             for name, store in (("Alice", alice), ("Bob", bob))]
    released = []
    # A watchdog thread, because a blocked event loop could not release the write itself.
    watchdog = threading.Timer(2, lambda: (released.append(time.monotonic()), release.set()))
    watchdog.start()
    workers["Alice"].events.put_nowait(Parked("Bob", alice_parked))
    workers["Bob"].events.put_nowait(Parked("Alice", bob_parked))
    try:
        for worker in workers.values():
            await asyncio.wait_for(worker.merged.wait(), 10)
    finally:
        watchdog.cancel()
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert workers["Bob"].merged_at < released[0], "Bob's merge waited on Alice's write"
    assert workers["Alice"].merged_at > released[0]
    assert (await put).status_code == 201
    assert [alice.get(n) for n in ("a.ics", "a2.ics", "b.ics")] == [b"from alice", b"held", b"from bob"]
    assert bob.get("a.ics") == b"from alice"
    server.server_close()


def _hub_that_never_reports(watches):
    """A Hub with no peers whose watch requests stay pending until cancelled."""
    async def handler(request):
        if request.url.path == "/session/peers":
            return httpx.Response(200, json={"peers": []})
        assert request.url.path == "/notifications/watch"
        watches.append(request)
        await asyncio.Event().wait()
    return httpx.AsyncClient(base_url="http://hub", transport=httpx.MockTransport(handler))


async def _publishing_agent(tmp_path, monkeypatch, publish):
    from caldav_spike import Store

    store = Store(tmp_path / "Alice", author="Alice")
    store.put("event.ics", b"original")
    (tmp_path / "cloud").mkdir()
    cloud = LocalFolderStore(tmp_path / "cloud")
    monkeypatch.setattr(sync_agent, "SmallSeaStore", lambda token, http: cloud)
    real = store.cal.publish

    async def recording_publish(target):
        await publish(real, target)

    monkeypatch.setattr(store.cal, "publish", recording_publish)
    return store, cloud


async def test_agent_publishes_while_a_watch_is_pending_then_stops(tmp_path, monkeypatch, spikes):
    published = asyncio.Event()

    async def publish(real, target):
        await real(target)
        published.set()

    store, cloud = await _publishing_agent(tmp_path, monkeypatch, publish)
    watches = []
    async with _hub_that_never_reports(watches) as http:
        agent = SyncAgent(store.cal, http, "token")
        agent.start()
        await asyncio.wait_for(published.wait(), 10)
        assert len(watches) == 1  # still waiting on the Hub
        await asyncio.wait_for(agent.stop(), 5)

    assert await store.cal.fetch(cloud, "Alice") == store.cal.repo.head()
    assert agent.events.empty()


async def test_agent_retries_a_failed_publish(tmp_path, monkeypatch, spikes):
    monkeypatch.setattr(sync_agent, "RETRY_DELAY", 0)
    published = asyncio.Event()
    attempts = []

    async def flaky_publish(real, target):
        attempts.append(target)
        if len(attempts) == 1:
            raise StoreTransportError("connection lost")
        await real(target)
        published.set()

    store, cloud = await _publishing_agent(tmp_path, monkeypatch, flaky_publish)
    async with _hub_that_never_reports([]) as http:
        agent = SyncAgent(store.cal, http, "token")
        agent.start()
        await asyncio.wait_for(published.wait(), 10)
        await agent.stop()

    failure = agent.events.get_nowait()
    assert isinstance(failure, SyncFailed) and failure.what == "publish"
    assert await store.cal.fetch(cloud, "Alice") == store.cal.repo.head()
