"""Move 8 probe: what publication and sibling adoption actually establish.

Moves 2-6 modeled publication outcomes, retry, merge and route-content
binding in the models' own terms. This probe puts the same schedules through
the real boundary: Manager's `push_note_to_self` / `refresh_note_to_self`,
Cod Sync's publication envelope, the Hub session transport and a local MinIO
store. The questions are plan.md's next-work item 1:

  * lost acknowledgments -- what a device may claim after one
  * superseding refusals -- what evidence the refusal carries and whether the
    device can adopt it
  * publication finding a descendant -- historical inclusion against current
    selection, for related and unrelated sibling changes
  * complete route content -- what a sibling actually receives
  * generic publication -- whether it can carry a private or refused candidate

The two-installation setup is Move 7's, imported rather than copied so that
`probe_interrupted_finalization.py` keeps running unchanged.

These probes are not micro tests. They run against current code and fix
nothing; what they assert is the observed behavior, defect or not.
"""
import pathlib

import pytest
import small_sea_hub.backend as SmallSea
from cod_sync.protocol import CodSync
from cod_sync.store import PublicationOutcomeUnknownError, SmallSeaStore
from fastapi.testclient import TestClient
from small_sea_hub.server import app
from small_sea_manager import provisioning
from small_sea_manager.manager import TeamManager

from probe_interrupted_finalization import (
    TEAM,
    _announcements,
    _bucket_exists,
    _core_allocation,
    _pause_before_storage_exists,
    _two_installations,
)


def _manager_b(root_b, alice_hex):
    """A live Manager for the second installation, on its own backend.

    Both installations share one FastAPI app object, so whichever backend was
    installed last is the one a `TeamManager` reaches. Every switch of acting
    device goes through this helper or its A-side twin.
    """
    backend_b = SmallSea.SmallSeaBackend(
        root_dir=str(root_b), auto_approve_sessions=True
    )
    app.state.backend = backend_b
    http_b = TestClient(app)
    return TeamManager(root_b, alice_hex, _http_client=http_b)


def _reattach(root, alice_hex):
    """Point the shared app back at `root`'s backend and hand back a Manager."""
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)
    return TeamManager(root, alice_hex, _http_client=http)


class _PublishRecorder:
    """Calls through to the real `CodSync.publish` and keeps every outcome.

    `push_note_to_self` discards the `PublishResult`, so the disposition and
    the observed head are not visible from any Manager return value. Recording
    them here observes the boundary without changing what crosses it.
    """

    def __init__(self, monkeypatch):
        self.results = []
        self.errors = []
        real = CodSync.publish

        def recording(inner_self, *args, **kwargs):
            try:
                result = real(inner_self, *args, **kwargs)
            except Exception as exn:
                self.errors.append(exn)
                raise
            self.results.append(result)
            return result

        monkeypatch.setattr(CodSync, "publish", recording)

    @property
    def last(self):
        return self.results[-1]


def _cloud_storage_rows(root, alice_hex):
    return provisioning.list_cloud_storage(root, alice_hex)


def test_publication_finding_a_descendant_reports_nothing_to_the_device(
    playground_dir, minio_server_gen, monkeypatch
):
    """A's re-push lands on B's descendant and A keeps routing to its own.

    A publishes, B adopts and deliberately replaces the route, B publishes.
    A -- which never refreshed -- pushes again with nothing of its own
    outstanding. Cod Sync reports `already_present`, which is true and says
    only that the store's head *contains* A's head. A's own state, and every
    Manager report it can read, still name A's replaced location.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.reconcile_team_route(TEAM, new_location=True)
    _, allocation_a = _core_allocation(root_a, alice_hex)
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()
    assert _core_allocation(root_b, alice_hex)[1] == allocation_a
    manager_b.reconcile_team_route(TEAM, new_location=True)
    _, allocation_b = _core_allocation(root_b, alice_hex)
    assert allocation_b["location"] != allocation_a["location"]
    manager_b.push_note_to_self()

    manager_a = _reattach(root_a, alice_hex)
    recorder = _PublishRecorder(monkeypatch)
    returned = manager_a.push_note_to_self()

    # The publication is an ordinary success. Its disposition is the only
    # place the descendant appears, and the Manager returns None either way.
    assert returned is None
    assert recorder.last.disposition == "already_present"
    assert recorder.last.observed_head != recorder.last.attempted_head

    # A's state is historically included in the store and is not the current
    # selection. Nothing A can read locally distinguishes the two.
    assert _core_allocation(root_a, alice_hex)[1] == allocation_a
    assert manager_a.core_storage_allocation(TEAM)["allocation"] == allocation_a


def test_a_descendant_holding_an_unrelated_change_is_indistinguishable(
    playground_dir, minio_server_gen, monkeypatch
):
    """The same `already_present` when B's descendant touches no route."""
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.reconcile_team_route(TEAM, new_location=True)
    _, allocation_a = _core_allocation(root_a, alice_hex)
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()
    # An unrelated Core change: a second registered account, no route touched.
    manager_b.add_cloud_storage(
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    manager_b.push_note_to_self()
    assert _core_allocation(root_b, alice_hex)[1] == allocation_a

    manager_a = _reattach(root_a, alice_hex)
    recorder = _PublishRecorder(monkeypatch)
    manager_a.push_note_to_self()

    assert recorder.last.disposition == "already_present"
    assert recorder.last.observed_head != recorder.last.attempted_head


def _lose_the_acknowledgment(monkeypatch, land_the_write=True):
    """Turn the head write into the store's own "may have taken effect".

    `SmallSeaStore.put_latest_link` already raises
    `PublicationOutcomeUnknownError` when the Hub response is unreadable, so
    this stages the real error the real code path raises, once, and chooses
    whether the write it describes actually landed.
    """
    real = SmallSeaStore.put_latest_link
    state = {"used": False}

    def unacknowledged(self, data, expected_etag, link_uid=None):
        if state["used"]:
            return real(self, data, expected_etag, link_uid=link_uid)
        state["used"] = True
        if land_the_write:
            real(self, data, expected_etag, link_uid=link_uid)
        raise PublicationOutcomeUnknownError(
            "staged: the latest-link write may have taken effect",
            expected_etag=expected_etag,
            link_uid=link_uid,
        )

    monkeypatch.setattr(SmallSeaStore, "put_latest_link", unacknowledged)
    return state


def _adopted_count(root, alice_hex, manager):
    berth_id = bytes.fromhex(
        manager._open_note_to_self_session(mode="passthrough").session_info()["berth_id"]
    )
    return provisioning.get_note_to_self_adopted_signal_count(root, alice_hex, berth_id)


def test_a_landed_but_unacknowledged_publication_settles_to_already_present(
    playground_dir, minio_server_gen, monkeypatch
):
    """Cod Sync resolves the lost acknowledgment; the Manager records nothing.

    The Move 5 ledger entry says an unknown outcome is incomplete evidence and
    that a publication claim never weakens. Cod Sync's settlement pass is
    stronger than the model assumed here: because the landed write spent the
    conditional etag, the outcome is not unknown at all -- it settles to
    `already_present` on the device's own head. What the runtime does not do
    is keep that answer: `push_note_to_self` returns None and writes no
    marker, so the claim exists only for the duration of the call.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, alice_hex = setup["root_a"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.reconcile_team_route(TEAM, new_location=True)
    before = _adopted_count(root_a, alice_hex, manager_a)

    staged = _lose_the_acknowledgment(monkeypatch, land_the_write=True)
    recorder = _PublishRecorder(monkeypatch)
    assert manager_a.push_note_to_self() is None
    assert staged["used"]

    # Settlement reread the store and found this device's own head there.
    assert recorder.last.disposition == "already_present"
    assert recorder.last.observed_head == recorder.last.attempted_head
    assert recorder.errors == []

    # The write landed with notify=True, so the Hub counted a self-update that
    # the `already_present` early return does not acknowledge. The adopted
    # baseline stays where it was and a later refresh has to catch up.
    assert _adopted_count(root_a, alice_hex, manager_a) == before

    # Nothing durable on this device says the publication happened. push_team
    # writes `.ss_last_push` beside a team's Sync repo for both ordinary
    # dispositions; the NoteToSelf path writes no such marker, so the only
    # record of the settled head was the discarded return value.
    nts_dir = manager_a._note_to_self_repo_dir().parent
    assert nts_dir.is_dir()
    assert not list(nts_dir.glob(".ss_last_push"))
    assert manager_a._last_published_head("NoteToSelf") is None


def test_a_lost_write_leaves_the_outcome_genuinely_unresolved(
    playground_dir, minio_server_gen, monkeypatch
):
    """The same staged error over a write that did not land is unknown.

    The control for the case above: identical error at the identical call
    site, differing only in whether the bytes reached the store. Here the
    conditional etag is unspent, so settlement cannot close the write and Cod
    Sync raises `PublicationOutcomeUnresolvedError`. The Manager does not
    catch it, so the caller gets the typed uncertainty and the device stores
    no record of it.
    """
    from cod_sync.protocol import PublicationOutcomeUnresolvedError

    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, alice_hex = setup["root_a"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.reconcile_team_route(TEAM, new_location=True)
    before = _adopted_count(root_a, alice_hex, manager_a)

    _lose_the_acknowledgment(monkeypatch, land_the_write=False)
    with pytest.raises(PublicationOutcomeUnresolvedError) as raised:
        manager_a.push_note_to_self()

    exn = raised.value
    assert exn.attempted_head is not None
    assert exn.observed_head != exn.attempted_head
    assert _adopted_count(root_a, alice_hex, manager_a) == before

    # A plain retry converges: the staged failure is spent, the write lands.
    monkeypatch.undo()
    recorder = _PublishRecorder(monkeypatch)
    manager_a.push_note_to_self()
    assert recorder.last.disposition == "published"


def test_generic_publication_carries_a_refused_candidate_to_a_sibling(
    playground_dir, minio_server_gen, monkeypatch
):
    """A push about something else moves B's current selection to dead storage.

    A rotates into a location the provider never creates, so A holds a
    durable allocation whose bucket does not exist and a route report of
    `materialization_failed`. A then does something unrelated -- registers a
    second account -- and calls the generic `push_note_to_self`, which commits
    whatever core.db holds. B refreshes for the unrelated change and adopts
    the failed allocation as its current one, losing the working allocation
    that the replacement deleted.

    This is plan.md's "can generic publication silently publish a private or
    refused candidate": it can, because nothing between the failed
    materialization and the shared chain distinguishes a candidate from a
    selection.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.reconcile_team_route(TEAM, new_location=True)
    manager_a.push_note_to_self()
    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()
    _, working = _core_allocation(root_b, alice_hex)
    assert _bucket_exists(minio, working["location"])

    manager_a = _reattach(root_a, alice_hex)
    _pause_before_storage_exists(monkeypatch)
    report_a = manager_a.reconcile_team_route(TEAM, new_location=True)
    monkeypatch.undo()
    assert report_a == {"route": "pending", "route_reason": "materialization_failed"}
    _, refused = _core_allocation(root_a, alice_hex)
    assert not _bucket_exists(minio, refused["location"])

    # The unrelated change, and the generic push that carries both.
    manager_a.add_cloud_storage(
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    outcome = manager_b.refresh_note_to_self()
    assert outcome["integration"].outcome == "integrated"

    # B's current selection is now storage that does not exist, and the
    # working allocation is gone rather than retained as a predecessor.
    _, adopted = _core_allocation(root_b, alice_hex)
    assert adopted == refused
    assert not _bucket_exists(minio, adopted["location"])
    assert adopted["location"] != working["location"]

    # B's own report calls it `pending`, the same word Move 7 showed does not
    # separate a half-materialized allocation from a finished unsigned one.
    assert manager_b.core_storage_allocation(TEAM)["route"] == "pending"

    # B's signed evidence did not move with its selection. Announcements live
    # in the team Core chain and travel by `push_team`; allocations live in
    # NoteToSelf. Adopting over one of those changed which location B selects
    # without touching what B has attested to, and nothing orders the two
    # publications against each other.
    signed = [
        row.location
        for row in _announcements(
            root_b, alice_hex, setup["teammate_id"], setup["berth_id"]
        )
    ]
    assert signed, "B holds no announcement at all"
    assert adopted["location"] not in signed
    assert working["location"] not in signed


def test_the_published_route_is_three_fields_and_repair_needs_more(
    playground_dir, minio_server_gen, monkeypatch
):
    """A teammate's route is frozen in the announcement; a sibling's is joined.

    The Move 6 ledger entry asks that a selection record freeze "every account
    value a route depends on". Two different routes are at stake and the
    answer differs:

      * what a teammate consumes is `TransportEndpoint(protocol, url,
        location)`, and the signed announcement carries exactly those three
        plus identity and signature -- frozen, as the ledger requires;
      * what a sibling needs in order to *repair* is more than that, and none
        of the extra reaches it through the announcement. The allocation a
        device reads is a read-time JOIN of `berth_cloud_allocation` onto
        `cloud_storage`, and the credentials it materializes with are
        device-local and never published at all.
    """
    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.reconcile_team_route(TEAM, new_location=True)
    manager_a.push_note_to_self()
    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()

    # The announcement for the rotation is A's: signing is a team-Core act, so
    # it stays on the device that performed it until `push_team`.
    [announcement] = [
        row
        for row in _announcements(
            root_a, alice_hex, setup["teammate_id"], setup["berth_id"]
        )
        if row.location == _core_allocation(root_a, alice_hex)[1]["location"]
    ]

    # Everything a signed announcement freezes. The route part of it is the
    # three fields `TransportEndpoint` is built from.
    assert set(vars(announcement)) == {
        "announcement_id",
        "teammate_id",
        "berth_id",
        "protocol",
        "url",
        "location",
        "announced_at",
        "signer_key_id",
        "signature",
    }

    # What a device reads locally is wider, and it is derived at read time
    # from two independently mutable rows rather than frozen anywhere.
    _, allocation = _core_allocation(root_b, alice_hex)
    joined_in = {"protocol", "url", "client_id", "path_metadata"}
    assert joined_in <= set(allocation)
    unpublished = joined_in - set(vars(announcement))
    assert unpublished == {"client_id", "path_metadata"}

    # And the value a repair actually needs beyond all of that -- the secret
    # for the account -- is device-local: B connected it out of band in
    # setup, and `list_cloud_storage` reports that as its own fact.
    [account] = _cloud_storage_rows(root_b, alice_hex)
    assert account["credentials_on_this_device"] is True
    assert "secret_key" not in account


def _both_siblings_rotate(setup):
    """A and B each replace the same berth's route from a common base.

    The disconnected-siblings schedule Moves 3 and 5 assume throughout: both
    devices adopt the same predecessor, neither refreshes, both select.
    Returns the two allocations and a Manager for B, which pushes second.
    """
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.reconcile_team_route(TEAM, new_location=True)
    manager_a.push_note_to_self()
    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()
    _, base = _core_allocation(root_b, alice_hex)

    manager_b.reconcile_team_route(TEAM, new_location=True)
    _, allocation_b = _core_allocation(root_b, alice_hex)

    manager_a = _reattach(root_a, alice_hex)
    manager_a.reconcile_team_route(TEAM, new_location=True)
    _, allocation_a = _core_allocation(root_a, alice_hex)
    assert len({base["location"], allocation_a["location"], allocation_b["location"]}) == 3
    manager_a.push_note_to_self()

    return base, allocation_a, allocation_b, _manager_b(root_b, alice_hex)


def test_two_siblings_rotating_one_berth_cannot_merge_their_selections(
    playground_dir, minio_server_gen, monkeypatch
):
    """The union of two rotations violates one-allocation-per-berth.

    The refusal itself is well formed: Cod Sync parks the observed head and
    names the merge base, which is the "adopted, not quoted" evidence the
    Move 5 ledger asks for. What has no answer is the integration. Splice
    Merge unions rows by primary key, replacement inserts a new allocation row
    rather than updating one, and the schema says a berth has at most one
    allocation -- so the union of two deliberate selections is not a state the
    database will hold, and integration refuses on the constraint.

    This is the concrete shape of "merging published records is union". Union
    does not derive a route nobody chose, as the Move 6 model warned; it
    produces nothing at all, because no rule in the merge decides between two
    competing rows. That decision is exactly what the succession counter was
    for, and no counter exists in this schema.
    """
    from cod_sync.protocol import PublicationIntegrationRequiredError

    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_b, alice_hex = setup["root_b"], setup["alice_hex"]
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError) as raised:
        manager_b.push_note_to_self()
    refusal = raised.value

    # The refusal carries the evidence, and parks it under an immutable ref.
    assert refusal.observed_head != refusal.attempted_head
    assert refusal.merge_base not in (None, refusal.attempted_head)
    assert refusal.parked_ref
    assert refusal.imported is True
    assert [s.ref_name for s in manager_b.note_to_self_conflict_status()] == [
        refusal.parked_ref
    ]

    # And the integration that evidence exists for cannot run.
    [outcome] = manager_b.integrate_note_to_self().outcomes
    assert outcome.outcome == "constraint_refused"
    assert "UNIQUE constraint failed: berth_cloud_allocation.berth_id" in outcome.detail
    assert outcome.recorded_head is None

    # B keeps its own selection, unaware that it is unpublishable.
    assert _core_allocation(root_b, alice_hex)[1] == allocation_b
    assert manager_b.core_storage_allocation(TEAM)["route"] == "ready"


def test_the_refused_sibling_has_no_recovery_through_any_manager_operation(
    playground_dir, minio_server_gen, monkeypatch
):
    """Every ordinary next step leaves B diverged, refused and reporting ready.

    The control for the case above: the wedge is not an artifact of stopping
    at the first refusal. Refreshing, integrating, reconciling without a
    change, and rotating again each complete without raising, and none of them
    moves B off the constraint. Rotating again makes a third allocation, which
    is refused for the same reason.
    """
    from cod_sync.protocol import PublicationIntegrationRequiredError

    minio = minio_server_gen()
    workspace = pathlib.Path(playground_dir)
    setup = _two_installations(workspace, minio, {})
    root_b, alice_hex = setup["root_b"], setup["alice_hex"]
    base, allocation_a, allocation_b, manager_b = _both_siblings_rotate(setup)

    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()

    def still_refused():
        [outcome] = manager_b.integrate_note_to_self().outcomes
        assert outcome.outcome == "constraint_refused", outcome
        assert outcome.recorded_head is None

    # A refresh reports the same constraint rather than adopting anything.
    assert manager_b.refresh_note_to_self()["integration"].outcome == (
        "constraint_refused"
    )
    still_refused()

    # Reconciling with no change argument repairs nothing, because from B's
    # side nothing is broken.
    assert manager_b.reconcile_team_route(TEAM) == {
        "route": "ready",
        "route_reason": None,
    }
    still_refused()

    # A deliberate new choice -- the human resolution Move 3 modeled -- is
    # refused identically: a third row for the same berth is still a second row.
    assert manager_b.reconcile_team_route(TEAM, new_location=True)["route"] == "ready"
    third = _core_allocation(root_b, alice_hex)[1]
    assert third["location"] not in (
        base["location"],
        allocation_a["location"],
        allocation_b["location"],
    )
    still_refused()

    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()
    assert manager_b.core_storage_allocation(TEAM)["route"] == "ready"
