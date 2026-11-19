import asyncio
import contextlib
import json
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

    stores = {name: await Store.open(tmp_path / name, author=name) for name in ("Alice", "Bob")}
    cloud = {}
    for name in stores:
        (tmp_path / f"{name}-cloud").mkdir()
        cloud[name] = LocalFolderStore(tmp_path / f"{name}-cloud")

    alice, bob = stores["Alice"], stores["Bob"]
    event = "event.ics"
    await alice.put(event, b"original")
    await alice.cal.publish(cloud["Alice"])
    await bob.cal.fetch(cloud["Alice"], "Alice")
    await bob.cal.merge(demo.peer_ref("Alice"))
    await bob.put(event, b"updated")
    await bob.cal.publish(cloud["Bob"])

    token = alice.token
    head = await alice.cal.fetch(cloud["Bob"], "Bob")
    agent = _Agent()
    await demo.handle_event("Alice", alice, agent, {}, Parked("Bob", head))

    assert agent.publish_requests == 1
    assert alice.get(event) == b"updated"
    assert alice.changes_since(token) == ([event], [])
    assert alice.version(etag_of(b"updated")) == b"updated"
    assert (await Store.open(tmp_path / "Alice")).changes_since(token) == ([event], [])


@contextlib.asynccontextmanager
async def _caldav(path):
    """Alice's CalDAV server on a free port: yields the server, its serve() task, and an event's URL."""
    import two_teammates_demo as demo
    from caldav_spike import make_server

    server = await make_server(0, "alice", "tide", str(path))
    serve = asyncio.create_task(server.serve())
    await demo.until_started(server, serve)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield server, serve, f"http://127.0.0.1:{port}/dav/calendars/alice/team/"
    finally:
        server.should_exit = True
        await serve


def _hold_saves(monkeypatch, store):
    """Make the store's saves wait, where a real save waits on the Hub to sign, until release is set."""
    committing, release = asyncio.Event(), asyncio.Event()
    real_save = store.cal.save

    async def held_save(*args):
        committing.set()
        await release.wait()
        return await real_save(*args)

    monkeypatch.setattr(store.cal, "save", held_save)
    return committing, release


async def test_caldav_serves_head_digest_challenge_and_unsupported_methods(tmp_path, spikes):
    async with _caldav(tmp_path / "alice") as (server, _, url), httpx.AsyncClient() as http:
        await server.store.put("event.ics", b"body")
        challenge = await http.request("PROPFIND", url)
        head = await http.head(url + "event.ics", auth=("alice", "tide"))
        mkcol = await http.request("MKCOL", url + "x/", auth=("alice", "tide"))

    assert challenge.status_code == 401
    assert [o.split()[0] for o in challenge.headers.get_list("www-authenticate")] == ["Digest", "Basic"]
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == "4" and head.headers["etag"]
    assert mkcol.status_code == 405


async def test_caldav_decodes_an_event_name_once(tmp_path, spikes):
    names = {"meeting.ics": b"meeting", "meeting.ics%3Fdraft.ics": b"draft", "a%2541.ics": b"percent"}
    async with _caldav(tmp_path / "alice") as (server, _, url), httpx.AsyncClient() as http:
        for name, body in names.items():
            put = await http.put(url + name, content=body, auth=("alice", "tide"))
            assert put.status_code in (201, 204)
        got = {name: (await http.get(url + name, auth=("alice", "tide"))).content for name in names}

    assert got == names
    assert server.store.get("meeting.ics?draft.ics") == b"draft"
    assert server.store.get("a%41.ics") == b"percent"


async def test_stopping_caldav_lets_a_write_in_progress_finish(tmp_path, spikes, monkeypatch):
    import two_teammates_demo as demo

    async with _caldav(tmp_path / "alice") as (server, serve, url), httpx.AsyncClient() as http:
        store = server.store
        committing, release = _hold_saves(monkeypatch, store)
        put = asyncio.create_task(http.put(url + "event.ics", content=b"body", auth=("alice", "tide")))
        await asyncio.wait_for(committing.wait(), 5)

        stopping = asyncio.create_task(demo.stop_caldav([server], [serve]))
        await asyncio.sleep(0.2)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, 5)
        assert (await put).status_code == 201

    assert store.get("event.ics") == b"body"
    assert await store.cal.repo.has_commits()


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
    from caldav_spike import Store

    async with _caldav(tmp_path / "alice") as (server, _, url), httpx.AsyncClient() as http:
        alice, bob = server.store, await Store.open(tmp_path / "bob", author="Bob")
        clouds = {}
        for name in ("Alice", "Bob"):
            (tmp_path / f"{name}-cloud").mkdir()
            clouds[name] = LocalFolderStore(tmp_path / f"{name}-cloud")
        await alice.put("a.ics", b"from alice")
        await alice.cal.publish(clouds["Alice"])
        bob_parked = await bob.cal.fetch(clouds["Alice"], "Alice")
        await bob.put("b.ics", b"from bob")
        await bob.cal.publish(clouds["Bob"])
        alice_parked = await alice.cal.fetch(clouds["Bob"], "Bob")

        committing, release = _hold_saves(monkeypatch, alice)
        put = asyncio.create_task(http.put(url + "a2.ics", content=b"held", auth=("alice", "tide")))
        await asyncio.wait_for(committing.wait(), 5)

        workers = {"Alice": _Worker(), "Bob": _Worker()}
        tasks = [asyncio.create_task(demo.process_events(name, store, workers[name], {}))
                 for name, store in (("Alice", alice), ("Bob", bob))]
        released = []
        asyncio.get_running_loop().call_later(2, lambda: (released.append(time.monotonic()), release.set()))
        workers["Alice"].events.put_nowait(Parked("Bob", alice_parked))
        workers["Bob"].events.put_nowait(Parked("Alice", bob_parked))
        try:
            for worker in workers.values():
                await asyncio.wait_for(worker.merged.wait(), 10)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert (await put).status_code == 201

    assert workers["Bob"].merged_at < released[0], "Bob's merge waited on Alice's write"
    assert workers["Alice"].merged_at > released[0]
    assert [alice.get(n) for n in ("a.ics", "a2.ics", "b.ics")] == [b"from alice", b"held", b"from bob"]
    assert bob.get("a.ics") == b"from alice"


def _hub_with_slow_authority_view(release):
    """A fake Hub for HubSigner; it answers the authority-view request only once release is set."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/session/authority_view":
                release.wait()
            body = json.dumps({
                "/session/info": {"team_id": "team", "berth_id": "berth"},
                "/session/signing_key": {"public_key": "ssh-ed25519 AAAA"},
                "/session/authority_view": {"authority_view": "dmlldw"},
            }[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def test_a_slow_authority_view_stalls_only_its_own_teammate(tmp_path, spikes):
    """While Alice's merge commit waits for her Hub's authority view, Bob still merges."""
    import two_teammates_demo as demo
    from caldav_spike import Store

    from tide_table.hub_signer import HubSigner

    alice = await Store.open(tmp_path / "alice", author="Alice")
    bob = await Store.open(tmp_path / "bob", author="Bob")
    clouds = {}
    for name, store in (("Alice", alice), ("Bob", bob)):
        (tmp_path / f"{name}-cloud").mkdir()
        clouds[name] = LocalFolderStore(tmp_path / f"{name}-cloud")
        await store.put(f"{name}.ics", f"from {name}".encode())
        await store.cal.publish(clouds[name])
    # Independent histories on both sides, so each merge makes a merge commit.
    alice_parked = await alice.cal.fetch(clouds["Bob"], "Bob")
    bob_parked = await bob.cal.fetch(clouds["Alice"], "Alice")

    release = threading.Event()
    hub = _hub_with_slow_authority_view(release)
    url = f"http://127.0.0.1:{hub.server_address[1]}"
    async with httpx.AsyncClient(base_url=url) as http:
        signer = await HubSigner.open(url, "token", http)
        signer.env = None  # commits unsigned: this test is about the authority-view request
        alice = await Store.open(tmp_path / "alice", author="Alice", signer=signer)

        workers = {"Alice": _Worker(), "Bob": _Worker()}
        tasks = [asyncio.create_task(demo.process_events(name, store, workers[name], {}))
                 for name, store in (("Alice", alice), ("Bob", bob))]
        released = []
        # A watchdog thread, because a blocked event loop could not release the Hub itself.
        watchdog = threading.Timer(2, lambda: (released.append(time.monotonic()), release.set()))
        watchdog.start()
        workers["Alice"].events.put_nowait(Parked("Bob", alice_parked))
        await asyncio.sleep(0.2)  # Alice's merge is waiting on her Hub
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
            hub.shutdown()
            hub.server_close()

    assert workers["Bob"].merged_at < released[0], "Bob's merge waited on Alice's Hub"
    assert workers["Alice"].merged_at > released[0]
    assert alice.get("Bob.ics") == b"from Bob" and bob.get("Alice.ics") == b"from Alice"
    assert b"Small-Sea-Work-Context: " in (await alice.cal._git("log", "-1", "--format=%B")).stdout


async def test_stopping_workers_reports_a_merge_that_fails_after_cancellation(tmp_path, spikes, monkeypatch):
    import two_teammates_demo as demo
    from caldav_spike import Store

    alice = await Store.open(tmp_path / "alice", author="Alice")
    bob = await Store.open(tmp_path / "bob", author="Bob")
    (tmp_path / "cloud").mkdir()
    cloud = LocalFolderStore(tmp_path / "cloud")
    await alice.put("a.ics", b"from alice")
    await alice.cal.publish(cloud)
    parked = await bob.cal.fetch(cloud, "Alice")

    merging, go_on = asyncio.Event(), asyncio.Event()
    real_merge = bob.cal.merge

    async def slow_merge(ref):
        merging.set()
        await go_on.wait()
        return await real_merge(ref)

    def failing_record(before):
        raise OSError("disk full")

    monkeypatch.setattr(bob.cal, "merge", slow_merge)
    monkeypatch.setattr(bob, "record_changes_since", failing_record)
    worker = _Worker()
    task = asyncio.create_task(demo.process_events("Bob", bob, worker, {}))
    worker.events.put_nowait(Parked("Alice", parked))
    await asyncio.wait_for(merging.wait(), 5)

    stopping = asyncio.create_task(demo.stop_workers([task]))
    await asyncio.sleep(0.1)
    assert not stopping.done()  # waiting for the merge, not abandoning it
    go_on.set()
    failures = await asyncio.wait_for(stopping, 5)

    assert [str(f) for f in failures] == ["disk full"]
    assert bob.get("a.ics") == b"from alice"  # the merge itself finished
    assert not bob.lock.locked()


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

    store = await Store.open(tmp_path / "Alice", author="Alice")
    await store.put("event.ics", b"original")
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

    assert await store.cal.fetch(cloud, "Alice") == await store.cal.repo.head()
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
    assert await store.cal.fetch(cloud, "Alice") == await store.cal.repo.head()
