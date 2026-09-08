"""Multiple-location probe: what the runtime does with two readable locations.

plan.md step 2. The Move 10 models argued about route succession; this probe
asks the runtime what it already does when one participant's berth has two
authenticated cloud locations at once:

  * selection -- which announcement the read path uses, and whether the
    unselected location is ever attempted, including when the selected
    location has lost its bundles while another announced one is complete
  * divergence -- what two `CodSync` fetches through one pin ref leave behind
    when the two locations carry unrelated histories
  * source binding -- whether one chain walk stays at the location it began
    at when an announcement arrives between two of its object reads
  * ancestry and identical content at two locations
  * write placement -- where a device publishes while a sibling's newer
    announcement names somewhere else
  * the sender-key prerequisite, in both orders of distribution and upload

Two deliberate departures from the plan's wording, both recorded rather than
hidden:

  * The reader is device A, not a third participant. `_download_peer_file`
    selects by (teammate, berth) and reads anonymously; nothing on that path
    depends on the reader being a different participant. A real teammate would
    additionally need its membership certificate and the sibling's device
    certificate staged into the two other devices, because neither travels
    over the runtime's fetch path (#185 fetches and parks, it does not
    integrate). Those staged rows would not change the code under test. What
    it does cost: A already holds the objects at its own locations, so only
    the sibling's head is a genuine import.
  * Announcement delivery is staged by inserting the sibling's own authentic
    signed row into the reader's team DB, as `probe_delayed_signing.py` does.
    The runtime has no path that carries an announcement between devices.

One real operation is required for a sibling's location to be readable at all:
device B must redistribute its team sender key to device A *before* B
publishes. What that exercises is one ratchet state, and no more: a
distribution carries B's sender chain at its current iteration with no skipped
message keys, so objects uploaded at an earlier iteration stay unreadable
afterwards. `test_a_sender_key_distributed_after_the_upload_cannot_read_it`
holds that ordering fixed and records both failures. Nothing here establishes
a general requirement about reading a location's whole history; it establishes
what this ratchet state does, which is enough to say that reading a second
location is not only a routing question.

These probes are not micro tests. They run against current code and fix
nothing; what they assert is the observed behavior, defect or not.
"""
import pathlib
import sqlite3

import boto3
import pytest
import small_sea_hub.backend as SmallSea
from botocore.config import Config as BotoConfig
from cod_sync.format import decode_link
from cod_sync.protocol import CodSync, PinIntegrationRequiredError
from cod_sync.store import (
    LATEST_LINK_PATH,
    ObjectNotFoundError,
    PeerSenderKeyUnavailableError,
    PeerSmallSeaStore,
    StoreTransportError,
)
from small_sea_manager import provisioning
from small_sea_manager.manager import (
    CoreFetchRemoteError,
    CorePublicationMissingError,
    core_peer_latest_ref,
    core_peer_observation_ref,
)
from small_sea_manager.sender_keys import load_peer_sender_key, load_team_sender_key

from probe_delayed_signing import _insert_announcement
from probe_interrupted_finalization import (
    TEAM,
    _announcements,
    _core_allocation,
    _two_installations,
)
from probe_publication_adoption import _manager_b, _reattach

MAIN = "refs/heads/main"


def _s3(minio):
    return boto3.client(
        "s3",
        endpoint_url=minio["endpoint"],
        aws_access_key_id=minio["access_key"],
        aws_secret_access_key=minio["secret_key"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _keys(minio, bucket):
    listing = _s3(minio).list_objects_v2(Bucket=bucket)
    return sorted(item["Key"] for item in listing.get("Contents", []))


def _copy_bucket(minio, source, destination):
    """Put the same published chain at a second location.

    No runtime path publishes one head to two stores, so the identical-content
    schedule is staged at the provider. The bytes are the ones device A
    uploaded, ciphertext included.
    """
    client = _s3(minio)
    for key in _keys(minio, source):
        client.copy_object(
            Bucket=destination, Key=key, CopySource={"Bucket": source, "Key": key}
        )


def _delete_bundles(minio, bucket):
    client = _s3(minio)
    deleted = [key for key in _keys(minio, bucket) if key.startswith("B-")]
    for key in deleted:
        client.delete_object(Bucket=bucket, Key=key)
    return deleted


def _announcement_at(root, alice_hex, setup, location):
    [row] = [
        row
        for row in _announcements(
            root, alice_hex, setup["teammate_id"], setup["berth_id"]
        )
        if row.location == location
    ]
    return row


def _set_announcements(db_path, rows):
    """Replace the reader's announcements for this berth with exactly `rows`.

    Deliberately absolute: every test names the whole set it is reading with,
    so no test depends on what another one left behind.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM teammate_berth_storage_announcement")
        conn.commit()
    for row in rows:
        _insert_announcement(db_path, row)


def _peer_store(manager, teammate_id_hex):
    """The store `fetch_teammate_core` builds, without its ref policy."""
    session = manager._get_or_open_session(TEAM)
    return PeerSmallSeaStore(
        session.token,
        teammate_id_hex,
        base_url=manager.client._base_url,
        client=manager.client._http_client,
    )


class _RequestRecorder:
    """Record the location the Hub resolves for each peer object read.

    `_download_peer_file` selects again for every request and returns only the
    bytes, so the location one object came from is visible nowhere else. The
    patch is on the class, because `_reattach` and `_manager_b` each build a
    fresh backend.

    `after` runs once a download has returned, which is the only point at which
    a schedule can change what the *next* request will select.
    """

    def __init__(self, monkeypatch, after=None):
        self.requests = []
        self.after = after
        self._path = None
        backend = SmallSea.SmallSeaBackend
        real_download = backend._download_peer_file
        real_select = backend._select_teammate_berth_storage

        def download(inner_self, session_hex, teammate_id_hex, path):
            self._path = path
            try:
                result = real_download(inner_self, session_hex, teammate_id_hex, path)
            finally:
                self._path = None
            if self.after is not None:
                self.after(path)
            return result

        def select(inner_self, conn, team_id, teammate_id, berth_id):
            selection = real_select(inner_self, conn, team_id, teammate_id, berth_id)
            if self._path is not None:
                transport = selection.transport
                location = None if transport is None else transport.location
                self.requests.append((self._path, location))
            return selection

        monkeypatch.setattr(backend, "_download_peer_file", download)
        monkeypatch.setattr(backend, "_select_teammate_berth_storage", select)

    @property
    def locations(self):
        return [location for _path, location in self.requests]

    def reset(self):
        self.requests = []


def _redistribute_b_sender_key(root_a, root_b, alice_hex):
    """Hand device B's team sender key to device A."""
    state_a = provisioning.derive_team_join_state(root_a, alice_hex, TEAM)
    redistribution = provisioning.redistribute_sender_key(
        root_b, alice_hex, TEAM, target_device_key_ids=[state_a["device_key_id"]]
    )
    assert redistribution["skipped_device_key_ids_hex"] == []
    provisioning.receive_sender_key_distribution(
        root_a, alice_hex, TEAM, redistribution["artifacts"][0]["distribution_payload"]
    )
    return redistribution


def _two_locations(workspace, minio, distribute_sender_key=True):
    """Alice's Core berth with three announced locations, two of them written.

    Device A publishes L0, replaces it with X and publishes again, so X's head
    descends L0's. Device B -- which cloned the team before either replacement
    -- selects Y and publishes its own divergent head there. A's write
    destination is left at X.

    Announcement ids are uuid7, so they rank L0 < X < Y.

    `distribute_sender_key` orders B's sender-key redistribution before its
    first upload. Only the sender-key schedule sets it False; every other test
    needs Y readable.
    """
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.push_team(TEAM)
    head_l0 = manager_a._team_repo(TEAM).resolve_ref(MAIN)
    _, allocation_l0 = _core_allocation(root_a, alice_hex)
    ann_l0 = _announcement_at(root_a, alice_hex, setup, allocation_l0["location"])

    manager_a.reconcile_team_route(TEAM, new_location=True)
    manager_a.push_team(TEAM)
    head_x = manager_a._team_repo(TEAM).resolve_ref(MAIN)
    _, allocation_x = _core_allocation(root_a, alice_hex)
    ann_x = _announcement_at(root_a, alice_hex, setup, allocation_x["location"])

    manager_b = _manager_b(root_b, alice_hex)
    manager_b.reconcile_team_route(TEAM, new_location=True)
    if distribute_sender_key:
        # Before B's first upload: see the module docstring.
        _redistribute_b_sender_key(root_a, root_b, alice_hex)
    manager_b.push_team(TEAM)
    head_y = manager_b._team_repo(TEAM).resolve_ref(MAIN)
    _, allocation_y = _core_allocation(root_b, alice_hex)
    ann_y = _announcement_at(root_b, alice_hex, setup, allocation_y["location"])

    manager_a = _reattach(root_a, alice_hex)
    repo_a = manager_a._team_repo(TEAM)
    assert repo_a.is_ancestor(head_l0, head_x)
    assert not repo_a.has_commit(head_y), "device A already holds B's head"
    assert ann_l0.announcement_id < ann_x.announcement_id < ann_y.announcement_id

    return {
        **setup,
        "manager_a": manager_a,
        "manager_b": manager_b,
        "repo_a": repo_a,
        "reader_db": provisioning._team_db_path(root_a, alice_hex, TEAM),
        "teammate_id_hex": setup["teammate_id"].hex(),
        "head_l0": head_l0,
        "head_x": head_x,
        "head_y": head_y,
        "ann_l0": ann_l0,
        "ann_x": ann_x,
        "ann_y": ann_y,
        "location_l0": allocation_l0["location"],
        "location_x": allocation_x["location"],
        "location_y": allocation_y["location"],
    }


def _add_spare_location(stage):
    """Give device A a third location: materialized, announced, never written.

    Kept out of the shared setup because it also replaces A's write
    destination, which the write-placement schedule needs left at X.
    """
    manager_a = stage["manager_a"]
    manager_a.reconcile_team_route(TEAM, new_location=True)
    _, allocation_z = _core_allocation(stage["root_a"], stage["alice_hex"])
    ann_z = _announcement_at(
        stage["root_a"], stage["alice_hex"], stage, allocation_z["location"]
    )
    assert ann_z.announcement_id > stage["ann_y"].announcement_id
    stage["ann_z"] = ann_z
    stage["location_z"] = allocation_z["location"]
    return stage


def test_the_reader_uses_the_newest_announcement_and_reaches_one_location(
    playground_dir, minio_server_gen
):
    """Latest signature wins, and the older location is never consulted.

    This is the control the plan asks to be made visible: with X and Y both
    admissible, `select_effective_teammate_berth_storage` sorts by
    announcement id and `_download_peer_file` reads that one store. The Hub
    exposes no way to ask for the other one, so what the reader can obtain is
    exactly one of the two published histories.
    """
    minio = minio_server_gen()
    stage = _two_locations(pathlib.Path(playground_dir), minio)
    manager_a = stage["manager_a"]

    _set_announcements(stage["reader_db"], [stage["ann_x"], stage["ann_y"]])

    # Both stores hold a complete published chain right now.
    assert "latest-link.yaml" in _keys(minio, stage["location_x"])
    assert "latest-link.yaml" in _keys(minio, stage["location_y"])

    store = _peer_store(manager_a, stage["teammate_id_hex"])
    latest, _etag = store.get_latest_link()
    assert decode_link(latest).head == stage["head_y"]

    result = manager_a.fetch_teammate_core(TEAM, stage["teammate_id_hex"])
    assert result.observed_head_sha == stage["head_y"]
    assert result.current_head_sha == stage["head_y"]
    assert result.disposition == "created"

    # X's head is A's own commit, so its presence proves nothing; what the
    # fetch never did is name X at all. The one pin that moved is at Y.
    latest_ref = core_peer_latest_ref(stage["teammate_id_hex"])
    assert stage["repo_a"].resolve_ref(latest_ref) == stage["head_y"]


def test_an_empty_selected_location_is_not_backed_up_by_a_published_one(
    playground_dir, minio_server_gen
):
    """The newest announcement wins even when its store publishes nothing.

    Z exists at the provider -- `reconcile_team_route` materialized it -- and
    holds no chain. With X and Y both complete and reachable, the reader still
    reports that the teammate publishes no Core chain. There is no fan-out and
    no fallback: one announcement is chosen and one store is asked.
    """
    minio = minio_server_gen()
    stage = _add_spare_location(_two_locations(pathlib.Path(playground_dir), minio))
    manager_a = stage["manager_a"]

    _set_announcements(
        stage["reader_db"], [stage["ann_x"], stage["ann_y"], stage["ann_z"]]
    )
    assert _keys(minio, stage["location_z"]) == []

    with pytest.raises(CorePublicationMissingError):
        manager_a.fetch_teammate_core(TEAM, stage["teammate_id_hex"])

    latest_ref = core_peer_latest_ref(stage["teammate_id_hex"])
    assert stage["repo_a"].resolve_ref(latest_ref) is None

    # Both other locations were readable throughout.
    for announcement, head in (
        (stage["ann_x"], stage["head_x"]),
        (stage["ann_y"], stage["head_y"]),
    ):
        _set_announcements(stage["reader_db"], [announcement])
        store = _peer_store(manager_a, stage["teammate_id_hex"])
        assert decode_link(store.get_latest_link()[0]).head == head


def test_two_fetches_import_both_heads_and_leave_the_second_unreferenced(
    playground_dir, minio_server_gen
):
    """Cod Sync's own fetch imports a divergent head without recording it.

    Reading two locations is two `CodSync` instances over two stores; the
    protocol needs no new feature for that. Each fetch here runs against a
    fixed announcement set, which is the only schedule this claim covers --
    `test_an_announcement_arriving_mid_fetch_redirects_the_rest_of_the_walk`
    shows that separate instances do not by themselves bind a walk to one
    location. What this fetch does not do is preserve the second observation:
    the objects are imported, the pin refuses to move, and the only surviving
    name for the second head is the SHA carried in the exception.
    """
    minio = minio_server_gen()
    stage = _two_locations(pathlib.Path(playground_dir), minio)
    manager_a, repo_a = stage["manager_a"], stage["repo_a"]
    pin = "refs/probe/multiple-locations/latest"

    _set_announcements(stage["reader_db"], [stage["ann_x"]])
    first = CodSync(repo_a, _peer_store(manager_a, stage["teammate_id_hex"])).fetch(
        pin_to_ref=pin
    )
    assert first.observed_head == stage["head_x"]
    assert first.pin_disposition == "created"

    _set_announcements(stage["reader_db"], [stage["ann_y"]])
    with pytest.raises(PinIntegrationRequiredError) as raised:
        CodSync(repo_a, _peer_store(manager_a, stage["teammate_id_hex"])).fetch(
            pin_to_ref=pin
        )
    refusal = raised.value

    assert refusal.ref_name == pin
    assert refusal.current_sha == stage["head_x"]
    assert refusal.observed_head == stage["head_y"]
    assert refusal.link_uid

    # The bytes are here: the import happens before the pin is touched.
    assert repo_a.has_commit(stage["head_y"])
    assert repo_a.resolve_ref(pin) == stage["head_x"]
    assert stage["head_y"] not in repo_a.list_refs("refs/").values()


def test_the_manager_parks_the_head_the_protocol_leaves_unreferenced(
    playground_dir, minio_server_gen
):
    """The gap above is closed by the caller, not by `fetch`.

    `fetch_teammate_core` catches the refusal and writes an immutable
    observation ref at the divergent head, so both locations' heads survive
    under names. Preserving alternatives on the read side is already this
    caller's behavior; a fan-out design inherits it rather than inventing it.
    """
    minio = minio_server_gen()
    stage = _two_locations(pathlib.Path(playground_dir), minio)
    manager_a, repo_a = stage["manager_a"], stage["repo_a"]
    teammate_hex = stage["teammate_id_hex"]

    _set_announcements(stage["reader_db"], [stage["ann_x"]])
    assert manager_a.fetch_teammate_core(TEAM, teammate_hex).current_head_sha == (
        stage["head_x"]
    )

    _set_announcements(stage["reader_db"], [stage["ann_y"]])
    second = manager_a.fetch_teammate_core(TEAM, teammate_hex)

    assert second.observed_head_sha == stage["head_y"]
    assert second.current_head_sha == stage["head_x"]
    assert second.observation_ref_name is not None
    assert repo_a.resolve_ref(second.observation_ref_name) == stage["head_y"]
    assert second.observation_ref_name.startswith(
        core_peer_observation_ref(teammate_hex, "")
    )
    assert repo_a.resolve_ref(core_peer_latest_ref(teammate_hex)) == stage["head_x"]

    heads = {head.head_sha for head in manager_a.list_core_source_heads(TEAM)}
    assert {stage["head_x"], stage["head_y"]} <= heads


def test_an_announcement_arriving_mid_fetch_redirects_the_rest_of_the_walk(
    playground_dir, minio_server_gen, monkeypatch
):
    """One walk, two endpoints: the store binds to a teammate, not a location.

    `PeerSmallSeaStore` carries a teammate id, and the Hub selects again on
    every object request, so nothing holds a fetch to the location its
    latest-link came from. The schedule installs a second announcement in the
    gap between the latest-link read and the bundle read, and the recorder
    names the location of each request.

    Both outcomes are recorded. Redirected to a byte-identical copy the fetch
    succeeds and reports nothing unusual, having read two objects from two
    locations; redirected to the sibling's divergent chain it fails as a
    missing object, because the bundle the link names was never uploaded
    there. Source binding is therefore a Hub and store obligation; separate
    `CodSync` instances do not supply it.
    """
    minio = minio_server_gen()
    stage = _add_spare_location(_two_locations(pathlib.Path(playground_dir), minio))
    manager_a, repo_a = stage["manager_a"], stage["repo_a"]
    teammate_hex = stage["teammate_id_hex"]
    reader_db = stage["reader_db"]
    _copy_bucket(minio, stage["location_x"], stage["location_z"])

    def redirect_to(announcements):
        def hook(path):
            if path == LATEST_LINK_PATH:
                _set_announcements(reader_db, announcements)

        return hook

    recorder = _RequestRecorder(monkeypatch)

    # Z holds X's bytes, so the redirected read is served and the result is
    # indistinguishable from a fetch that never left X.
    _set_announcements(reader_db, [stage["ann_x"]])
    recorder.after = redirect_to([stage["ann_x"], stage["ann_z"]])
    copied = CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(
        pin_to_ref="refs/probe/multiple-locations/redirected-to-copy"
    )
    assert copied.observed_head == stage["head_x"]
    assert copied.pin_disposition == "created"
    assert recorder.requests[0] == (LATEST_LINK_PATH, stage["location_x"])
    assert len(recorder.requests) > 1
    assert recorder.locations[1:] == [stage["location_z"]] * (
        len(recorder.requests) - 1
    )

    # The same gap, redirected to a location publishing something else.
    recorder.reset()
    _set_announcements(reader_db, [stage["ann_x"]])
    recorder.after = redirect_to([stage["ann_x"], stage["ann_y"]])
    pin = "refs/probe/multiple-locations/redirected-to-sibling"
    with pytest.raises(ObjectNotFoundError):
        CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(pin_to_ref=pin)
    assert repo_a.resolve_ref(pin) is None
    assert recorder.requests[0] == (LATEST_LINK_PATH, stage["location_x"])
    assert recorder.locations[1:] == [stage["location_y"]]


def test_an_older_location_does_not_roll_the_pin_back(
    playground_dir, minio_server_gen
):
    """L0's head is X's ancestor; reading it after X changes nothing.

    Both orders are checked on separate pins. Ancestry here describes the two
    stored histories, not any claim that L0 has stopped receiving work.
    """
    minio = minio_server_gen()
    stage = _two_locations(pathlib.Path(playground_dir), minio)
    manager_a, repo_a = stage["manager_a"], stage["repo_a"]
    teammate_hex = stage["teammate_id_hex"]

    descendant_first = "refs/probe/multiple-locations/descendant-first"
    _set_announcements(stage["reader_db"], [stage["ann_x"]])
    CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(
        pin_to_ref=descendant_first
    )
    _set_announcements(stage["reader_db"], [stage["ann_l0"]])
    stale = CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(
        pin_to_ref=descendant_first
    )
    assert stale.observed_head == stage["head_l0"]
    assert stale.pin_disposition == "stale"
    assert stale.pinned_head == stage["head_x"]
    assert repo_a.resolve_ref(descendant_first) == stage["head_x"]

    ancestor_first = "refs/probe/multiple-locations/ancestor-first"
    created = CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(
        pin_to_ref=ancestor_first
    )
    assert created.pin_disposition == "created"
    _set_announcements(stage["reader_db"], [stage["ann_x"]])
    advanced = CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(
        pin_to_ref=ancestor_first
    )
    assert advanced.pin_disposition == "advanced"
    assert repo_a.resolve_ref(ancestor_first) == stage["head_x"]


def test_the_same_publication_at_two_locations_settles_on_one_head(
    playground_dir, minio_server_gen
):
    """Duplicate delivery through a second store is a no-op, in either order.

    The second location's bytes are X's, copied at the provider, so the two
    stores publish the same link uid and the same head. Deduplication here is
    ordinary chain validation -- the head is already held and the pin is
    already there -- not a comparison between sources.
    """
    minio = minio_server_gen()
    stage = _add_spare_location(_two_locations(pathlib.Path(playground_dir), minio))
    manager_a, repo_a = stage["manager_a"], stage["repo_a"]
    teammate_hex = stage["teammate_id_hex"]
    _copy_bucket(minio, stage["location_x"], stage["location_z"])
    assert _keys(minio, stage["location_z"]) == _keys(minio, stage["location_x"])

    for name, order in (
        ("x-then-copy", [stage["ann_x"], stage["ann_z"]]),
        ("copy-then-x", [stage["ann_z"], stage["ann_x"]]),
    ):
        pin = f"refs/probe/multiple-locations/{name}"
        dispositions = []
        for announcement in order:
            _set_announcements(stage["reader_db"], [announcement])
            result = CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(
                pin_to_ref=pin
            )
            assert result.observed_head == stage["head_x"], name
            dispositions.append(result.pin_disposition)
        assert dispositions == ["created", "unchanged"], name
        assert repo_a.resolve_ref(pin) == stage["head_x"]


def test_an_incomplete_selected_location_is_not_backed_up_by_a_known_one(
    playground_dir, minio_server_gen, monkeypatch
):
    """A complete alternative the reader knows about is still never attempted.

    The bundles are deleted from Y while its links stay, which is the shape a
    partial upload or a provider-side cleanup leaves behind. Y is the newest
    announcement and therefore the selection; X is announced to the same
    reader throughout and holds a complete chain. This is the fallback
    question stated properly: the alternative is authenticated, installed and
    readable, and the runtime never names it. The recorder shows every request
    of both attempts going to Y.
    """
    minio = minio_server_gen()
    stage = _two_locations(pathlib.Path(playground_dir), minio)
    manager_a, repo_a = stage["manager_a"], stage["repo_a"]
    teammate_hex = stage["teammate_id_hex"]
    pin = "refs/probe/multiple-locations/incomplete"

    assert _delete_bundles(minio, stage["location_y"])
    assert "latest-link.yaml" in _keys(minio, stage["location_y"])
    assert any(key.startswith("B-") for key in _keys(minio, stage["location_x"]))

    _set_announcements(stage["reader_db"], [stage["ann_x"], stage["ann_y"]])
    recorder = _RequestRecorder(monkeypatch)

    with pytest.raises(ObjectNotFoundError):
        CodSync(repo_a, _peer_store(manager_a, teammate_hex)).fetch(pin_to_ref=pin)
    assert repo_a.resolve_ref(pin) is None

    # The Manager reports the same failure as a remote fetch error, not as a
    # chain defect and not as an absent publication.
    with pytest.raises(CoreFetchRemoteError):
        manager_a.fetch_teammate_core(TEAM, teammate_hex)
    assert repo_a.resolve_ref(core_peer_latest_ref(teammate_hex)) is None

    assert set(recorder.locations) == {stage["location_y"]}


def test_a_siblings_newer_announcement_does_not_move_this_devices_writes(
    playground_dir, minio_server_gen
):
    """A publishes to its own location while Y is the selected announcement.

    `_require_own_storage_announcement` compares the selection with the
    allocation being written to and, when they differ, accepts this device's
    own signed announcement instead. So the write side already keeps one local
    destination per device: hearing a sibling's newer announcement does not
    redirect writes, which is the default the multiple-location experiment
    proposes and the runtime already has.

    The commit A publishes here contains the staged row, which is what
    integrating an authentic announcement would have produced anyway.
    """
    minio = minio_server_gen()
    stage = _two_locations(pathlib.Path(playground_dir), minio)
    manager_a, repo_a = stage["manager_a"], stage["repo_a"]
    teammate_hex = stage["teammate_id_hex"]

    # A's write destination is X; Y is the announcement the selector returns.
    _set_announcements(stage["reader_db"], [stage["ann_x"], stage["ann_y"]])
    manager_a.create_invitation(TEAM, invitee_label="probe")
    assert manager_a.push_team(TEAM) == "published"
    head_after = repo_a.resolve_ref(MAIN)
    assert head_after != stage["head_x"]

    # The publication landed at A's own location, and Y's head is exactly
    # where B left it.
    _set_announcements(stage["reader_db"], [stage["ann_x"]])
    store_x = _peer_store(manager_a, teammate_hex)
    assert decode_link(store_x.get_latest_link()[0]).head == head_after

    _set_announcements(stage["reader_db"], [stage["ann_y"]])
    store_y = _peer_store(manager_a, teammate_hex)
    assert decode_link(store_y.get_latest_link()[0]).head == stage["head_y"]


def test_a_sender_key_distributed_after_the_upload_cannot_read_it(
    playground_dir, minio_server_gen
):
    """The late-distribution case, held fixed rather than met once in setup.

    The same setup with B's redistribution withheld. Before it, A holds no
    sender key for B's device at all and the Hub says so as a named
    prerequisite. After it, A holds B's chain at the iteration B has now
    reached, and a distribution message carries an iteration and a chain key
    and nothing else -- so the objects B uploaded at earlier iterations stay
    unreadable even though B itself still holds their message keys.

    What this establishes is the ordering in one ratchet state: a reader can
    read what B publishes from the distributed iteration onward, and nothing
    earlier. It is not a general statement about reading a location's history,
    which would need a recovery path for the earlier keys to exist and fail.

    The second failure is also misreported twice. `decrypt_group_payload`
    raises a bare `ValueError`, which is not the `SenderKeyUnavailableExn` the
    Hub route classifies as `peer_sender_key_unavailable`, and the client
    store then reads it as a failed request. In-process the escaping exception
    reaches the store directly; over a real HTTP boundary it would arrive as a
    500 instead. Either way the reader is told the request did not complete,
    when the bytes arrived and cannot be decrypted.
    """
    minio = minio_server_gen()
    stage = _two_locations(
        pathlib.Path(playground_dir), minio, distribute_sender_key=False
    )
    manager_a, root_a, root_b = stage["manager_a"], stage["root_a"], stage["root_b"]
    alice_hex = stage["alice_hex"]
    _set_announcements(stage["reader_db"], [stage["ann_y"]])

    with pytest.raises(PeerSenderKeyUnavailableError):
        _peer_store(manager_a, stage["teammate_id_hex"]).get_latest_link()

    _redistribute_b_sender_key(root_a, root_b, alice_hex)

    with pytest.raises(StoreTransportError) as raised:
        _peer_store(manager_a, stage["teammate_id_hex"]).get_latest_link()
    assert "No skipped key for iteration" in str(raised.value)

    # Why: B kept a message key for every iteration it published at, and the
    # distribution message carried none of them.
    team_id, _self_in_team = provisioning._team_row(root_a, alice_hex, TEAM)
    published = load_team_sender_key(
        provisioning.device_local_db_path(root_b, alice_hex), team_id
    )
    received = load_peer_sender_key(
        provisioning.device_local_db_path(root_a, alice_hex),
        team_id,
        published.sender_device_key_id,
    )
    assert sorted(published.skipped_message_keys) == list(range(published.iteration))
    assert received.iteration == published.iteration
    assert received.skipped_message_keys == {}
