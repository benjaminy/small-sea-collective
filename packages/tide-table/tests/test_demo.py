import pathlib

import pytest
from cod_sync.store import LocalFolderStore, StoreTransportError


def test_failed_publish_preserves_local_sync_changes(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1] / "spikes"))
    import two_teammates_demo as demo
    from caldav_spike import Store, etag_of

    stores = {name: Store(tmp_path / name, author=name) for name in ("Alice", "Bob")}
    cloud_paths = {name: tmp_path / f"{name}-cloud" for name in stores}
    for path in cloud_paths.values():
        path.mkdir()
    cloud = {name: LocalFolderStore(path) for name, path in cloud_paths.items()}
    monkeypatch.setattr(demo, "SmallSeaStore", lambda token, **_: cloud[token])
    monkeypatch.setattr(demo, "PeerSmallSeaStore", lambda token, teammate, **_: cloud[teammate])

    alice, bob = stores["Alice"], stores["Bob"]
    event = "event.ics"
    alice.put(event, b"original")
    alice.cal.publish(cloud["Alice"])
    bob.cal.fetch(cloud["Alice"], "Alice")
    bob.cal.merge(demo.peer_ref("Alice"))
    bob.put(event, b"updated")
    bob.cal.publish(cloud["Bob"])

    token = alice.token
    before = alice.cal.repo.head()
    publish = alice.cal.publish

    def fail_after_merge(store):
        if alice.cal.repo.head() != before:
            raise StoreTransportError("connection lost")
        return publish(store)

    monkeypatch.setattr(alice.cal, "publish", fail_after_merge)
    team = {name: {"hub": None, "token": name, "teammate_id": name} for name in stores}
    args = ("Alice", "Bob", stores, team)
    with pytest.raises(StoreTransportError, match="connection lost"):
        demo.sync(*args)

    assert alice.get(event) == b"updated"
    assert alice.changes_since(token) == ([event], [])
    assert alice.version(etag_of(b"updated")) == b"updated"
    assert Store(tmp_path / "Alice").changes_since(token) == ([event], [])

    monkeypatch.setattr(alice.cal, "publish", publish)
    merged_token = alice.token
    demo.sync(*args)

    assert alice.token == merged_token
    assert bob.cal.fetch(cloud["Alice"], "Alice") == alice.cal.repo.head()
