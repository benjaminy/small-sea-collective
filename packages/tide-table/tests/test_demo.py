import pathlib
import threading

from cod_sync.store import LocalFolderStore, StoreTransportError

import tide_table.sync_agent as sync_agent
from tide_table.sync_agent import Parked, SyncAgent, SyncFailed


class _Agent:
    def __init__(self):
        self.publish_requests = 0

    def request_publish(self):
        self.publish_requests += 1


def test_merge_records_changes_before_publishing(tmp_path, monkeypatch):
    """A merge's changes reach CalDAV clients whether or not the publish that follows succeeds."""
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "spikes"))
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
    alice.cal.publish(cloud["Alice"])
    bob.cal.fetch(cloud["Alice"], "Alice")
    bob.cal.merge(demo.peer_ref("Alice"))
    bob.put(event, b"updated")
    bob.cal.publish(cloud["Bob"])

    token = alice.token
    head = alice.cal.fetch(cloud["Bob"], "Bob")
    agent = _Agent()
    demo.handle_event("Alice", alice, agent, {}, Parked("Bob", head))

    assert agent.publish_requests == 1
    assert alice.get(event) == b"updated"
    assert alice.changes_since(token) == ([event], [])
    assert alice.version(etag_of(b"updated")) == b"updated"
    assert Store(tmp_path / "Alice").changes_since(token) == ([event], [])


def test_agent_retries_a_failed_publish(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "spikes"))
    from caldav_spike import Store

    store = Store(tmp_path / "Alice", author="Alice")
    store.put("event.ics", b"original")
    (tmp_path / "cloud").mkdir()
    cloud = LocalFolderStore(tmp_path / "cloud")
    monkeypatch.setattr(sync_agent, "SmallSeaStore", lambda token, **_: cloud)
    monkeypatch.setattr(sync_agent, "RETRY_DELAY", 0)

    published = threading.Event()
    attempts = []
    publish = store.cal.publish

    def flaky_publish(target):
        attempts.append(target)
        if len(attempts) == 1:
            raise StoreTransportError("connection lost")
        publish(target)
        published.set()

    monkeypatch.setattr(store.cal, "publish", flaky_publish)
    agent = SyncAgent(store.cal, "http://127.0.0.1:1", "token")
    threading.Thread(target=agent._publish, daemon=True).start()
    agent.request_publish()

    assert published.wait(10)
    failure = agent.events.get_nowait()
    assert isinstance(failure, SyncFailed) and failure.what == "publish"
    assert store.cal.fetch(cloud, "Alice") == store.cal.repo.head()
