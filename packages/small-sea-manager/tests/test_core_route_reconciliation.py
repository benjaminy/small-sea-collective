"""Micro tests for established-teammate Core route reconciliation (issue #208).

One operation repairs a missing route and carries out an intentional provider,
account, or location change, with no acceptance artifact anywhere in it. The
device's allocation is its own storage decision; the announcement history is
the append-only signed record peers route by. A change replaces the first
before it appends to the second, and every replacement gets a fresh allocation
generation that the Hub and the publisher both check.

Peer delivery of the new announcement is separate sync work; so is copying
existing data out of the old location.
"""

import pathlib
import sqlite3

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from click.testing import CliRunner
from fastapi.testclient import TestClient
from small_sea_client.client import SmallSeaHubUnavailable
from small_sea_hub.cloud_errors import (
    CloudAnnouncementMissingExn,
    MaterializationOutcome,
)
from small_sea_hub.server import app
from small_sea_manager.cli import cli
from small_sea_manager.manager import TeamManager
from small_sea_manager.web import create_app
from small_sea_note_to_self.db import list_admission_acceptance_artifacts
from small_sea_note_to_self.ids import uuid7
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public
from wrasse_trust.transport import (
    TeammateBerthStorageAnnouncement,
    canonical_teammate_berth_storage_announcement_bytes,
)

TEAM = "ProjectX"


# --------------------------------------------------------------------------- #
# Setup helpers
# --------------------------------------------------------------------------- #


def _team_db(root, participant_hex) -> pathlib.Path:
    return root / "Participants" / participant_hex / TEAM / "Sync" / "core.db"


def _add_storage(root, participant_hex, name):
    cloud_dir = root / name
    cloud_dir.mkdir()
    return provisioning.add_cloud_storage(
        root, participant_hex, protocol="localfolder", url=str(cloud_dir)
    )


def _established_alice(root, *, storage=True):
    """A creator: admission finalized, no acceptance artifact, ever."""
    alice_hex = provisioning.create_new_participant(root, "Alice")
    if storage:
        _add_storage(root, alice_hex, "alice-cloud")
    provisioning.create_team(root, alice_hex, TEAM)
    return alice_hex


class _FakeSession:
    """Stands in for a Hub team session during reconciliation."""

    def __init__(self, *, error=None, on_ready=None):
        self.error = error
        self.on_ready = on_ready
        self.calls = 0

    def ensure_cloud_ready(self):
        self.calls += 1
        if self.on_ready is not None:
            self.on_ready()
        if self.error is not None:
            raise self.error


def _manager(root, participant_hex, *, session=None):
    mgr = TeamManager(root, participant_hex)

    def _open(team, mode="encrypted"):
        assert session is not None, "reconciliation opened an unexpected session"
        return session

    mgr._get_or_open_session = _open
    return mgr


def _state(root, participant_hex):
    return provisioning.derive_team_join_state(root, participant_hex, TEAM)


def _allocation(root, participant_hex):
    return _state(root, participant_hex)["allocation"]


def _announcement_rows(root, participant_hex):
    state = _state(root, participant_hex)
    conn = sqlite3.connect(str(_team_db(root, participant_hex)))
    try:
        return conn.execute(
            "SELECT announcement_id, location, signer_key_id "
            "FROM teammate_berth_storage_announcement "
            "WHERE teammate_id = ? AND berth_id = ? ORDER BY announcement_id",
            (state["self_in_team"], state["core_berth_id"]),
        ).fetchall()
    finally:
        conn.close()


def _effective_location(root, participant_hex):
    """The location a peer's selection logic would route to."""
    state = _state(root, participant_hex)
    engine = provisioning._sqlite_engine(_team_db(root, participant_hex))
    try:
        with engine.begin() as conn:
            selection = provisioning.selected_teammate_berth_storage_announcement(
                conn,
                state["self_in_team"],
                state["core_berth_id"],
                team_id=state["team_id"],
            )
    finally:
        engine.dispose()
    return None if selection.transport is None else selection.transport.location


def _delete_announcements(root, participant_hex):
    conn = sqlite3.connect(str(_team_db(root, participant_hex)))
    with conn:
        conn.execute("DELETE FROM teammate_berth_storage_announcement")
    conn.close()


def _delete_team_certificates(root, participant_hex):
    """Strip the trust view so this device's own key is no longer trusted."""
    conn = sqlite3.connect(str(_team_db(root, participant_hex)))
    with conn:
        conn.execute("DELETE FROM key_certificate")
    conn.close()


def _insert_foreign_signed_announcement(
    root, participant_hex, location, *, announcement_id=None
):
    """Append a well-formed row signed by a key this team never trusted."""
    state = _state(root, participant_hex)
    foreign_key, private_key = generate_key_pair(ProtectionLevel.DAILY)
    public_key = foreign_key.public_key
    announcement = TeammateBerthStorageAnnouncement(
        announcement_id=announcement_id if announcement_id is not None else uuid7(),
        teammate_id=state["self_in_team"],
        berth_id=state["core_berth_id"],
        protocol="localfolder",
        url=state["allocation"]["url"],
        location=location,
        announced_at=provisioning._now_iso(),
        signer_key_id=key_id_from_public(public_key),
        signature=b"",
    )
    signature = provisioning._sign_bytes(
        private_key, canonical_teammate_berth_storage_announcement_bytes(announcement)
    )
    conn = sqlite3.connect(str(_team_db(root, participant_hex)))
    with conn:
        conn.execute(
            "INSERT INTO teammate_berth_storage_announcement "
            "(announcement_id, teammate_id, berth_id, protocol, url, location, "
            "announced_at, signer_key_id, signature) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                announcement.announcement_id,
                announcement.teammate_id,
                announcement.berth_id,
                announcement.protocol,
                announcement.url,
                announcement.location,
                announcement.announced_at,
                announcement.signer_key_id,
                signature,
            ),
        )
    conn.close()
    return announcement


# --------------------------------------------------------------------------- #
# a-b. Repair, and a creator who configured storage late
# --------------------------------------------------------------------------- #


def test_an_established_teammate_repairs_a_missing_announcement(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)
    _delete_announcements(root, alice_hex)
    assert _state(root, alice_hex)["route"] == "pending"

    session = _FakeSession()
    report = _manager(root, alice_hex, session=session).reconcile_team_route(TEAM)

    assert report == {"route": "ready", "route_reason": None}
    # Repair, not rotation: same allocation, and the route is the same locator.
    assert _allocation(root, alice_hex)["id"] == before["id"]
    assert _effective_location(root, alice_hex) == before["location"]
    assert session.calls == 1
    # The established path never reads or needs an acceptance artifact.
    assert list_admission_acceptance_artifacts(
        root, alice_hex, _state(root, alice_hex)["team_id"]
    ) == []


def test_a_creator_who_configures_storage_late_publishes_a_route(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root, storage=False)
    assert _allocation(root, alice_hex) is None

    mgr = _manager(root, alice_hex, session=_FakeSession())
    assert mgr.reconcile_team_route(TEAM)["route_reason"] == "storage_not_configured"

    _add_storage(root, alice_hex, "alice-cloud-late")
    report = mgr.reconcile_team_route(TEAM)

    assert report == {"route": "ready", "route_reason": None}
    allocation = _allocation(root, alice_hex)
    assert allocation is not None
    assert _effective_location(root, alice_hex) == allocation["location"]


# --------------------------------------------------------------------------- #
# c. Account selection
# --------------------------------------------------------------------------- #


def test_multiple_registered_accounts_require_a_choice(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root, storage=False)
    _add_storage(root, alice_hex, "cloud-one")
    second = _add_storage(root, alice_hex, "cloud-two")

    session = _FakeSession()
    mgr = _manager(root, alice_hex, session=session)
    report = mgr.reconcile_team_route(TEAM)

    assert report == {"route": "pending", "route_reason": "storage_choice_required"}
    assert _allocation(root, alice_hex) is None
    # Choosing where Core data lives is never done silently for an established
    # teammate, so no provider was contacted.
    assert session.calls == 0

    chosen = mgr.reconcile_team_route(TEAM, cloud_storage_id=second)
    assert chosen == {"route": "ready", "route_reason": None}
    assert _allocation(root, alice_hex)["cloud_storage_id"] == second


@pytest.mark.parametrize("bad_id", ["not-hex", "00ff"])
def test_an_invalid_account_fails_before_touching_the_allocation(
    playground_dir, bad_id
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)

    session = _FakeSession()
    mgr = _manager(root, alice_hex, session=session)
    with pytest.raises(ValueError):
        mgr.reconcile_team_route(TEAM, cloud_storage_id=bad_id, new_location=True)

    assert _allocation(root, alice_hex) == before
    assert session.calls == 0


# --------------------------------------------------------------------------- #
# d-e. Intentional change
# --------------------------------------------------------------------------- #


def test_an_account_change_is_not_skipped_by_a_ready_route(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    assert _state(root, alice_hex)["route"] == "ready"
    before = _allocation(root, alice_hex)
    second = _add_storage(root, alice_hex, "cloud-two")

    report = _manager(root, alice_hex, session=_FakeSession()).reconcile_team_route(
        TEAM, cloud_storage_id=second
    )

    assert report == {"route": "ready", "route_reason": None}
    after = _allocation(root, alice_hex)
    assert after["cloud_storage_id"] == second
    assert after["id"] != before["id"]
    assert after["location"] != before["location"]
    # Appended, never rewritten: the old route stays in the signed history.
    assert len(_announcement_rows(root, alice_hex)) == 2
    assert _effective_location(root, alice_hex) == after["location"]


def test_selecting_the_current_account_is_not_a_rotation(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)

    report = _manager(root, alice_hex, session=_FakeSession()).reconcile_team_route(
        TEAM, cloud_storage_id=before["cloud_storage_id"]
    )

    assert report == {"route": "ready", "route_reason": None}
    assert _allocation(root, alice_hex) == before
    assert len(_announcement_rows(root, alice_hex)) == 1


class _StubAdapter:
    def __init__(self, outcome):
        self._outcome = outcome

    def materialize(self):
        return self._outcome


def _hub_manager(root, participant_hex, outcome, monkeypatch):
    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    monkeypatch.setattr(
        SmallSea.SmallSeaBackend,
        "_make_storage_adapter_from_record",
        lambda self, ss_session, cloud: _StubAdapter(outcome),
    )
    return TeamManager(root, participant_hex, _http_client=TestClient(app)), backend


def test_a_rotation_publishes_the_reread_locator_not_the_provisional_one(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)
    final_location = "provider-final-locator"

    mgr, _backend = _hub_manager(
        root,
        alice_hex,
        MaterializationOutcome("materialized_with_locator", final_location),
        monkeypatch,
    )
    report = mgr.reconcile_team_route(TEAM, new_location=True)

    assert report == {"route": "ready", "route_reason": None}
    after = _allocation(root, alice_hex)
    assert after["id"] != before["id"]
    assert after["location"] == final_location
    # The Manager-generated provisional name never reaches the signed history.
    assert _effective_location(root, alice_hex) == final_location
    assert [row[1] for row in _announcement_rows(root, alice_hex)] == [
        before["location"],
        final_location,
    ]


# --------------------------------------------------------------------------- #
# f-g. Failure after the replacement is durable
# --------------------------------------------------------------------------- #


def test_publication_failure_leaves_the_old_route_and_retries_without_flags(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)

    def _boom(*args, **kwargs):
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(
        provisioning, "publish_teammate_berth_storage_announcement", _boom
    )
    session = _FakeSession()
    mgr = _manager(root, alice_hex, session=session)
    report = mgr.reconcile_team_route(TEAM, new_location=True)

    assert report == {"route": "pending", "route_reason": "route_preparation_error"}
    replacement = _allocation(root, alice_hex)
    assert replacement["id"] != before["id"]
    # Peers keep routing to the old location: the announcement was never touched.
    assert _effective_location(root, alice_hex) == before["location"]

    monkeypatch.undo()
    # A retry is not a second rotation: no change arguments, same replacement.
    retried = mgr.reconcile_team_route(TEAM)
    assert retried == {"route": "ready", "route_reason": None}
    converged = _allocation(root, alice_hex)
    assert converged["id"] == replacement["id"]
    assert converged["location"] == replacement["location"]
    assert _effective_location(root, alice_hex) == replacement["location"]


def test_the_gap_blocks_this_devices_own_hub_cloud_io(playground_dir, monkeypatch):
    """P4's accepted divergence, stated as a test rather than left implicit."""
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)

    backend = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    app.state.backend = backend
    mgr = TeamManager(root, alice_hex, _http_client=TestClient(app))
    session_hex = mgr._get_or_open_session(TEAM).token
    ss_session = backend._lookup_session(session_hex)

    # Before the replacement, the device's own announcement admits its I/O.
    backend._require_own_storage_announcement(
        ss_session, backend._resolve_berth_cloud_or_raise(ss_session)
    )

    provisioning.resolve_berth_cloud_allocation_intent(
        root, alice_hex, _state(root, alice_hex)["core_berth_id"], new_location=True
    )

    with pytest.raises(CloudAnnouncementMissingExn):
        backend._require_own_storage_announcement(
            ss_session, backend._resolve_berth_cloud_or_raise(ss_session)
        )


def test_commit_failure_is_retryable_without_recontacting_the_provider(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    session = _FakeSession()
    mgr = _manager(root, alice_hex, session=session)
    repo = mgr._team_repo(TEAM)
    real_commit_paths = repo.commit_paths
    attempts = 0

    def _fail_once(paths, message):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected commit failure")
        return real_commit_paths(paths, message)

    monkeypatch.setattr(repo, "commit_paths", _fail_once)
    monkeypatch.setattr(mgr, "_team_repo", lambda team_name: repo)

    first = mgr.reconcile_team_route(TEAM, new_location=True)
    assert first == {"route": "pending", "route_reason": "route_preparation_error"}
    assert repo.work_tree_paths_differ_from_head(["core.db"])
    rows_after_failure = _announcement_rows(root, alice_hex)

    retried = mgr.reconcile_team_route(TEAM)
    assert retried == {"route": "ready", "route_reason": None}
    assert not repo.work_tree_paths_differ_from_head(["core.db"])
    # The materialized announcement was committed, not published again.
    assert _announcement_rows(root, alice_hex) == rows_after_failure
    assert session.calls == 1


# --------------------------------------------------------------------------- #
# h. Trust
# --------------------------------------------------------------------------- #


def test_an_untrusted_signers_row_is_repaired_by_the_current_device(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    allocation = _allocation(root, alice_hex)
    _delete_announcements(root, alice_hex)
    stale = _insert_foreign_signed_announcement(root, alice_hex, "abandoned-location")
    assert _effective_location(root, alice_hex) is None

    report = _manager(root, alice_hex, session=_FakeSession()).reconcile_team_route(
        TEAM
    )

    assert report == {"route": "ready", "route_reason": None}
    assert _effective_location(root, alice_hex) == allocation["location"]
    ids = [row[0] for row in _announcement_rows(root, alice_hex)]
    assert stale.announcement_id in ids and len(ids) == 2


def test_an_untrusted_maximum_id_cannot_block_route_repair(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    allocation = _allocation(root, alice_hex)
    _delete_announcements(root, alice_hex)
    _insert_foreign_signed_announcement(
        root,
        alice_hex,
        "abandoned-location",
        announcement_id=b"\xff" * 16,
    )
    assert _effective_location(root, alice_hex) is None

    report = _manager(root, alice_hex, session=_FakeSession()).reconcile_team_route(
        TEAM
    )

    assert report == {"route": "ready", "route_reason": None}
    assert _effective_location(root, alice_hex) == allocation["location"]


def test_an_untrusted_current_device_reports_without_contacting_anyone(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    monkeypatch.setattr(
        provisioning,
        "publish_teammate_berth_storage_announcement",
        lambda *a, **k: pytest.fail("an untrusted device published a route"),
    )
    mgr = _manager(root, alice_hex, session=None)
    state = _state(root, alice_hex)
    monkeypatch.setattr(
        provisioning,
        "derive_team_join_state",
        lambda *a, **k: {**state, "admission": "pending"},
    )

    report = mgr.reconcile_team_route(TEAM, new_location=True)

    assert report == {"route": "pending", "route_reason": "current_device_untrusted"}
    # The rejected request never became a durable rotation.
    monkeypatch.undo()
    assert _allocation(root, alice_hex) == state["allocation"]


def test_an_untrusted_current_device_still_rejects_invalid_account(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)
    state = _state(root, alice_hex)
    monkeypatch.setattr(
        provisioning,
        "derive_team_join_state",
        lambda *a, **k: {**state, "admission": "pending"},
    )

    with pytest.raises(ValueError):
        _manager(root, alice_hex, session=None).reconcile_team_route(
            TEAM, cloud_storage_id="not-hex", new_location=True
        )

    monkeypatch.undo()
    assert _allocation(root, alice_hex) == before


# --------------------------------------------------------------------------- #
# i-j. Idempotence and mid-flight replacement
# --------------------------------------------------------------------------- #


def test_reconciling_twice_with_no_change_is_a_no_op(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    mgr = _manager(root, alice_hex, session=_FakeSession())

    assert mgr.reconcile_team_route(TEAM) == {"route": "ready", "route_reason": None}
    allocation = _allocation(root, alice_hex)
    rows = _announcement_rows(root, alice_hex)

    assert mgr.reconcile_team_route(TEAM) == {"route": "ready", "route_reason": None}
    assert _allocation(root, alice_hex) == allocation
    assert _announcement_rows(root, alice_hex) == rows


def test_a_replacement_during_hub_setup_is_not_announced(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)
    berth_id = _state(root, alice_hex)["core_berth_id"]
    _delete_announcements(root, alice_hex)

    def _replace_mid_flight():
        provisioning.resolve_berth_cloud_allocation_intent(
            root, alice_hex, berth_id, new_location=True
        )

    session = _FakeSession(on_ready=_replace_mid_flight)
    report = _manager(root, alice_hex, session=session).reconcile_team_route(TEAM)

    assert report == {"route": "pending", "route_reason": "allocation_conflict"}
    # The stale operation signed nothing for an allocation it never materialized.
    assert _announcement_rows(root, alice_hex) == []
    assert _allocation(root, alice_hex)["id"] != before["id"]


def test_a_replacement_seen_in_the_final_read_back_is_not_reported_ready(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    _delete_announcements(root, alice_hex)
    berth_id = _state(root, alice_hex)["core_berth_id"]
    mgr = _manager(root, alice_hex, session=_FakeSession())
    real_commit = mgr._commit_prepared_route

    def _replace_after_publication(team_name):
        result = real_commit(team_name)
        provisioning.resolve_berth_cloud_allocation_intent(
            root, alice_hex, berth_id, new_location=True
        )
        return result

    monkeypatch.setattr(mgr, "_commit_prepared_route", _replace_after_publication)
    report = mgr.reconcile_team_route(TEAM)

    assert report == {"route": "pending", "route_reason": "allocation_conflict"}


def test_allocation_intent_does_not_adopt_a_post_transaction_replacement(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    berth_id = _state(root, alice_hex)["core_berth_id"]
    first = _allocation(root, alice_hex)["cloud_storage_id"]
    second = _add_storage(root, alice_hex, "cloud-two")
    real_get = provisioning.get_berth_cloud_allocation_for_berth
    interleave = True

    def _replace_before_post_transaction_reread(root_dir, participant_hex, berth):
        nonlocal interleave
        if interleave:
            interleave = False
            provisioning.resolve_berth_cloud_allocation_intent(
                root_dir,
                participant_hex,
                berth,
                cloud_storage_id_hex=second,
            )
        return real_get(root_dir, participant_hex, berth)

    monkeypatch.setattr(
        provisioning,
        "get_berth_cloud_allocation_for_berth",
        _replace_before_post_transaction_reread,
    )
    allocation, reason = provisioning.resolve_berth_cloud_allocation_intent(
        root,
        alice_hex,
        berth_id,
        cloud_storage_id_hex=first,
        new_location=True,
    )

    assert reason is None
    assert allocation["cloud_storage_id"] == first


def test_a_ready_racing_replacement_is_reported_as_an_allocation_conflict(
    playground_dir, monkeypatch
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    berth_id = _state(root, alice_hex)["core_berth_id"]
    state = _state(root, alice_hex)
    real_resolve = provisioning.resolve_berth_cloud_allocation_intent

    def _resolve_then_publish_a_replacement(*args, **kwargs):
        intended, reason = real_resolve(*args, **kwargs)
        replacement, replacement_reason = real_resolve(
            root, alice_hex, berth_id, new_location=True
        )
        assert replacement_reason is None
        provisioning.publish_teammate_berth_storage_announcement(
            root,
            alice_hex,
            TEAM,
            state["self_in_team"],
            berth_id,
            replacement,
        )
        return intended, reason

    monkeypatch.setattr(
        provisioning,
        "resolve_berth_cloud_allocation_intent",
        _resolve_then_publish_a_replacement,
    )
    report = _manager(root, alice_hex, session=None).reconcile_team_route(
        TEAM, new_location=True
    )

    assert report == {"route": "pending", "route_reason": "allocation_conflict"}


# --------------------------------------------------------------------------- #
# Replacement ordering (P1)
# --------------------------------------------------------------------------- #


def test_a_replacement_announcement_outranks_the_row_it_supersedes(
    playground_dir, monkeypatch
):
    """Selection orders by announcement ID, not by insertion order.

    UUIDv7 is random within a millisecond, so a low-drawing replacement would
    otherwise be published, committed, and then ignored by every peer.
    """
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)
    low_id = bytes.fromhex("00" * 16)
    monkeypatch.setattr(provisioning, "uuid7", lambda: low_id)

    report = _manager(root, alice_hex, session=_FakeSession()).reconcile_team_route(
        TEAM, new_location=True
    )

    assert report == {"route": "ready", "route_reason": None}
    after = _allocation(root, alice_hex)
    rows = _announcement_rows(root, alice_hex)
    assert [row[1] for row in rows] == [before["location"], after["location"]]
    assert _effective_location(root, alice_hex) == after["location"]


def test_uuid7_successor_preserves_version_and_variant(monkeypatch):
    lower_bound = bytes.fromhex("0000000000017fffbfffffffffffffff")
    monkeypatch.setattr(provisioning, "uuid7", lambda: b"\x00" * 16)

    successor = provisioning._uuid7_after(lower_bound)

    assert successor > lower_bound
    assert successor[6] >> 4 == 0x7
    assert successor[8] >> 6 == 0x2


# --------------------------------------------------------------------------- #
# CLI and web surfaces
# --------------------------------------------------------------------------- #


def _cli(root, participant_hex, *args):
    return CliRunner().invoke(
        cli,
        ["--root-dir", str(root), "--participant-hex", participant_hex, *args],
    )


@pytest.fixture()
def fake_session(monkeypatch):
    """Give every TeamManager built by a surface a stubbed Hub session."""
    session = _FakeSession()
    monkeypatch.setattr(
        TeamManager, "_get_or_open_session", lambda self, team, mode="encrypted": session
    )
    return session


def test_cli_lists_registered_storage_ids(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    storage_id = _allocation(root, alice_hex)["cloud_storage_id"]

    result = _cli(root, alice_hex, "cloud-storage")

    assert result.exit_code == 0
    assert storage_id in result.stdout


def test_cli_reconcile_reports_a_ready_route(playground_dir, fake_session):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    _delete_announcements(root, alice_hex)

    result = _cli(root, alice_hex, "reconcile-route", TEAM)

    assert result.exit_code == 0
    assert "route: ready" in result.stderr
    assert _state(root, alice_hex)["route"] == "ready"


@pytest.mark.parametrize(
    "setup, reason, guidance",
    [
        (
            lambda root, alice_hex: _add_storage(root, alice_hex, "cloud-two"),
            "storage_choice_required",
            "--cloud-storage-id",
        ),
        (
            lambda root, alice_hex: _delete_team_certificates(root, alice_hex),
            "current_device_untrusted",
            "not trusted",
        ),
    ],
)
def test_cli_renders_guidance_for_the_new_reasons(
    playground_dir, fake_session, setup, reason, guidance
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root, storage=False)
    _add_storage(root, alice_hex, "cloud-one")
    setup(root, alice_hex)

    result = _cli(root, alice_hex, "reconcile-route", TEAM)

    # A typed pending route is an outcome, not a usage failure.
    assert result.exit_code == 0
    assert reason in result.stderr
    assert guidance in result.stderr
    assert f"reconcile-route {TEAM}" in result.stderr
    assert "--new-location" not in result.stderr


def test_cli_rejects_an_unregistered_account_without_changing_anything(
    playground_dir, fake_session
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)

    result = _cli(
        root, alice_hex, "reconcile-route", TEAM, "--cloud-storage-id", "00ff",
        "--new-location",
    )

    assert result.exit_code != 0
    assert _allocation(root, alice_hex) == before


def _web(root, participant_hex):
    return TestClient(create_app(str(root), participant_hex))


def test_team_detail_offers_the_core_storage_form(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    allocation = _allocation(root, alice_hex)

    body = _web(root, alice_hex).get(f"/teams/{TEAM}").text

    assert f"/teams/{TEAM}/reconcile-route" in body
    assert 'name="cloud_storage_id"' in body
    assert 'name="new_location"' in body
    assert allocation["location"] in body


def test_web_reconcile_publishes_the_selected_account(playground_dir, fake_session):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    second = _add_storage(root, alice_hex, "cloud-two")

    resp = _web(root, alice_hex).post(
        f"/teams/{TEAM}/reconcile-route",
        data={"cloud_storage_id": second, "new_location": "1"},
    )

    assert resp.status_code == 200
    after = _allocation(root, alice_hex)
    assert after["cloud_storage_id"] == second
    assert after["location"] in resp.text
    assert _effective_location(root, alice_hex) == after["location"]


def test_web_false_rotation_value_does_not_generate_a_new_location(
    playground_dir, fake_session
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)

    resp = _web(root, alice_hex).post(
        f"/teams/{TEAM}/reconcile-route",
        data={"new_location": "false"},
    )

    assert resp.status_code == 200
    assert _allocation(root, alice_hex) == before


def test_web_renders_a_pending_reason_with_a_retry_that_drops_the_intent(
    playground_dir, fake_session
):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root, storage=False)
    _add_storage(root, alice_hex, "cloud-one")
    _add_storage(root, alice_hex, "cloud-two")

    resp = _web(root, alice_hex).post(f"/teams/{TEAM}/reconcile-route", data={})

    assert resp.status_code == 200
    assert "storage_choice_required" in resp.text
    retry = resp.text.split('<form hx-post="/teams/')[1]
    # The retry form carries no rotation intent: repeating one would generate
    # another location instead of resuming this allocation.
    assert 'name="new_location"' not in retry.split("</form>")[0]


def test_web_renders_an_invalid_account_as_input_error(playground_dir, fake_session):
    root = pathlib.Path(playground_dir)
    alice_hex = _established_alice(root)
    before = _allocation(root, alice_hex)

    resp = _web(root, alice_hex).post(
        f"/teams/{TEAM}/reconcile-route",
        data={"cloud_storage_id": "00ff", "new_location": "1"},
    )

    assert resp.status_code == 200
    assert "notice-err" in resp.text
    assert _allocation(root, alice_hex) == before
