"""Move 9 probe: what a human can see and do about Move 8's wedge.

Move 8 established that two siblings rotating one berth cannot merge their
selections: B's publication is refused, the observed head is parked, and every
ordinary Manager operation leaves B refused while reporting `route: ready`.
The review's first answer was automatic adoption. The 2026-09-07 discussion
rejected that as a prerequisite: if a person's own device is blocked, an
explicit pause they resolve when they choose may be the intended outcome.

That makes different questions load-bearing, and this probe asks them of the
runtime rather than of a model:

  * what B shows -- whether any report explains the block, and whether the
    competing selections survive somewhere a human could read
  * what pauses -- whether the pause is scoped to the disputed berth or stops
    the whole NoteToSelf channel, in both directions
  * choosing A's allocation -- the smallest operation that makes that choice
    effective, and whether unrelated work rides along with it
  * choosing B's allocation -- whether the runtime can express that choice
  * pausing again -- what a second disagreement after a resolution looks like
  * what does not pause -- whether team data and announcements keep moving
    while the allocation channel is stopped

The two-installation setup and the wedge schedule are Move 7's and Move 8's,
imported rather than copied so those probes keep running unchanged.

These probes are not micro tests. They run against current code and fix
nothing; what they assert is the observed behavior, defect or not.
"""
import pathlib
import sqlite3
import tempfile

import pytest
from cod_sync.protocol import PublicationIntegrationRequiredError
from small_sea_manager import provisioning
from small_sea_note_to_self.db import (
    SHARED_DB_FILENAME,
    attached_note_to_self_connection,
)

from probe_interrupted_finalization import TEAM, _core_allocation, _two_installations
from probe_publication_adoption import _both_siblings_rotate, _manager_b, _reattach


def _allocations_at(repo, rev):
    """Every berth allocation held by one committed NoteToSelf state.

    A parked ref names a whole `core.db` blob, so a competing sibling's
    selection is recoverable by reading it out of Git. Nothing in the Manager
    API does this; it is what a human's tooling would have to do.
    """
    with tempfile.TemporaryDirectory(prefix="probe-parked-") as work:
        path = pathlib.Path(work) / "parked.db"
        repo.blob_at(rev, SHARED_DB_FILENAME, path)
        conn = sqlite3.connect(path)
        try:
            return [
                {"berth_id": berth.hex(), "location": location}
                for berth, location in conn.execute(
                    "SELECT berth_id, location FROM berth_cloud_allocation"
                )
            ]
        finally:
            conn.close()


def _drop_allocation(root, alice_hex, berth_id):
    """Withdraw this device's competing selection, and nothing else.

    Standing in for the operation a human resolution would need. It is a raw
    row delete because no Manager operation withdraws a selection: `reconcile`
    can only replace one with another.
    """
    with attached_note_to_self_connection(root, alice_hex) as conn:
        conn.execute("DELETE FROM berth_cloud_allocation WHERE berth_id = ?", (berth_id,))
        conn.commit()


def test_the_wedged_device_reports_ready_and_the_alternatives_are_only_in_git(
    playground_dir, minio_server_gen, monkeypatch
):
    """B shows no conflict; the evidence is real but outside the Manager API.

    The parked ref is the whole of what B is told, and it names a commit, not
    a disagreement about a berth. Both competing selections and their common
    predecessor do survive -- A's inside the parked blob, the base at the merge
    base, B's live -- so a human decision has the evidence it needs, and no
    report offers it.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_b, alice_hex, berth_id = setup["root_b"], setup["alice_hex"], setup["berth_id"]
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError) as raised:
        manager_b.push_note_to_self()
    refusal = raised.value

    # What B is told: one parked commit, and a route that looks fine.
    [status] = manager_b.note_to_self_conflict_status()
    assert status.head_sha == refusal.observed_head
    assert manager_b.core_storage_allocation(TEAM) == {
        "allocation": allocation_b,
        "route": "ready",
        "admission": "finalized",
    }

    # What survives: three selections, none of them reachable through Manager.
    repo = manager_b._note_to_self_repo()
    parked = {a["location"] for a in _allocations_at(repo, refusal.observed_head)}
    ancestor = {a["location"] for a in _allocations_at(repo, refusal.merge_base)}
    assert allocation_a["location"] in parked
    assert base["location"] in ancestor
    assert _core_allocation(root_b, alice_hex)[1]["location"] == allocation_b["location"]
    assert berth_id == bytes.fromhex(allocation_b["berth_id"])


def test_the_pause_stops_the_whole_channel_in_both_directions(
    playground_dir, minio_server_gen, monkeypatch
):
    """One disputed berth blocks unrelated work, outbound and inbound.

    The pause is not scoped to the disagreement. B cannot publish a second
    cloud account it registered afterwards, because publication is refused
    before the change is even considered; and B cannot adopt an unrelated
    change A makes, because integration refuses on the allocation constraint
    and applies no rows at all.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()

    # Outbound: an unrelated registration cannot leave the device.
    before = len(manager_b.list_cloud_storage())
    manager_b.add_cloud_storage(protocol="s3", url=minio["endpoint"] + "/second-b")
    assert len(manager_b.list_cloud_storage()) == before + 1
    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()

    # Inbound: an unrelated registration on A cannot reach B either.
    manager_a = _reattach(root_a, alice_hex)
    manager_a.add_cloud_storage(protocol="s3", url=minio["endpoint"] + "/second-a")
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    assert manager_b.refresh_note_to_self()["integration"].outcome == "constraint_refused"
    assert [s["url"] for s in manager_b.list_cloud_storage()] == [
        minio["endpoint"],
        minio["endpoint"] + "/second-b",
    ]


def test_choosing_the_siblings_allocation_takes_an_operation_no_manager_offers(
    playground_dir, minio_server_gen, monkeypatch
):
    """Withdrawing B's selection unblocks everything; nothing exposes that.

    "Adopt A's choice" is one row delete away from working: with B's competing
    row gone, the parked head applies, the unrelated account A registered
    arrives in the same operation, and B publishes again. The delete has no
    Manager operation -- `reconcile` replaces a selection, never withdraws one
    -- so the smallest effective human choice is not expressible in the API.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex, berth_id = (
        setup["root_a"], setup["root_b"], setup["alice_hex"], setup["berth_id"]
    )
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()

    # A does something unrelated while B is paused, so the resolution has to
    # carry a change that nobody disagrees about.
    manager_a = _reattach(root_a, alice_hex)
    manager_a.add_cloud_storage(protocol="s3", url=minio["endpoint"] + "/second-a")
    manager_a.push_note_to_self()
    manager_b = _manager_b(root_b, alice_hex)
    assert manager_b.refresh_note_to_self()["integration"].outcome == "constraint_refused"

    _drop_allocation(root_b, alice_hex, berth_id)
    [outcome] = manager_b.integrate_note_to_self().outcomes
    assert outcome.outcome == "integrated", outcome
    assert outcome.recorded_head is not None

    # B now holds A's allocation and A's unrelated change.
    assert _core_allocation(root_b, alice_hex)[1]["location"] == allocation_a["location"]
    assert minio["endpoint"] + "/second-a" in [
        s["url"] for s in manager_b.list_cloud_storage()
    ]
    assert manager_b.note_to_self_conflict_status() == []

    # Adopting the row is not the whole choice: B's own signed announcement
    # still names the location it just gave up, so the route is pending until
    # B signs for the allocation it adopted. That repair is an ordinary
    # no-argument reconcile, and it is the second half of the resolution.
    assert manager_b.core_storage_allocation(TEAM)["route"] == "pending"
    assert manager_b.reconcile_team_route(TEAM) == {
        "route": "ready",
        "route_reason": None,
    }
    manager_b.push_note_to_self()


def test_choosing_this_devices_allocation_is_not_expressible(
    playground_dir, minio_server_gen, monkeypatch
):
    """B cannot keep the location it chose; the only move is a third one.

    After adopting A's head to clear the block, reinstating B's own selection
    would mean naming a location that already exists and is already
    materialized. `reconcile_team_route` has no location parameter by design,
    so the human who wanted B's choice gets a fresh rotation instead: a third
    location, a third bucket, and B's materialized storage abandoned.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_b, alice_hex, berth_id = setup["root_b"], setup["alice_hex"], setup["berth_id"]
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()
    _drop_allocation(root_b, alice_hex, berth_id)
    [outcome] = manager_b.integrate_note_to_self().outcomes
    assert outcome.outcome == "integrated", outcome

    # There is no operation that says "the one I already chose".
    report = manager_b.reconcile_team_route(TEAM, new_location=True)
    assert report["route"] == "ready"
    resumed = _core_allocation(root_b, alice_hex)[1]
    assert resumed["location"] not in (
        base["location"], allocation_a["location"], allocation_b["location"]
    )
    manager_b.push_note_to_self()


def test_a_second_disagreement_pauses_again_and_parks_another_ref(
    playground_dir, minio_server_gen, monkeypatch
):
    """Resolving once buys nothing structural: the next rotation wedges too.

    A resolution is a state, not a rule. With the first conflict cleared, the
    same schedule run a second time produces the same refusal on the same
    constraint, and the repository accumulates a second parked ref while the
    first stays outstanding or not according to ancestry alone.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex, berth_id = (
        setup["root_a"], setup["root_b"], setup["alice_hex"], setup["berth_id"]
    )
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()
    _drop_allocation(root_b, alice_hex, berth_id)
    assert manager_b.integrate_note_to_self().outcomes[0].outcome == "integrated"
    manager_b.push_note_to_self()

    # Second round from the resolved state: both rotate again, A wins the race.
    manager_b.reconcile_team_route(TEAM, new_location=True)
    second_b = _core_allocation(root_b, alice_hex)[1]
    manager_a = _reattach(root_a, alice_hex)
    manager_a.refresh_note_to_self()
    manager_a.reconcile_team_route(TEAM, new_location=True)
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    with pytest.raises(PublicationIntegrationRequiredError) as raised:
        manager_b.push_note_to_self()
    assert "berth_cloud_allocation" in str(
        manager_b.integrate_note_to_self().outcomes[0].detail
    )
    assert _core_allocation(root_b, alice_hex)[1] == second_b
    assert raised.value.parked_ref in [
        s.ref_name for s in manager_b.note_to_self_conflict_status()
    ]


def test_the_team_chain_keeps_moving_while_the_allocation_channel_is_paused(
    playground_dir, minio_server_gen, monkeypatch
):
    """The pause holds one channel, and the signed one is not it.

    Allocations travel over NoteToSelf; announcements are written into the team
    Core chain and travel by `push_team`. B is refused on the first and still
    signs and pushes on the second, so a teammate can be told about a route
    that B's own sibling will never learn it selected.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_b, alice_hex, teammate_id, berth_id = (
        setup["root_b"], setup["alice_hex"], setup["teammate_id"], setup["berth_id"]
    )
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()

    from probe_interrupted_finalization import _announcements

    signed = _announcements(root_b, alice_hex, teammate_id, berth_id)
    assert allocation_b["location"] in {a.location for a in signed}
    assert manager_b.push_team(TEAM)
