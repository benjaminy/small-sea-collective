"""Micro tests for Manager Core-storage UX (issue #139).

Issue #208 delivered the reconciliation operation itself. What remains, and
what these tests witness, is how the Manager *presents* it: a Hub session and
current Core-route state are separate facts, each typed cloud precondition gets
the repair that actually fixes it, and a push that the Hub refuses explains
itself instead of echoing the Hub's JSON envelope.

The MinIO witness at the end is the sequence neither existing test covered: a
team created before any storage, repaired through the Manager's own surfaces,
and then pushed.
"""

import pathlib

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from click.testing import CliRunner
from fastapi.testclient import TestClient
from markupsafe import escape
from small_sea_client.client import SmallSeaCloudStorageRequired
from small_sea_hub.server import app as hub_app
from small_sea_manager import cli as cli_module
from small_sea_manager import web as web_module
from small_sea_manager.cli import cli
from small_sea_manager.manager import ROUTE_REASON_BY_CLOUD_REASON, TeamManager
from small_sea_manager.web import create_app

TEAM = "ProjectX"

#: The typed Hub preconditions issue #139 names, and the route reason each one
#: becomes. `announcement_missing` is deliberately absent: it is not a
#: precondition the user configures.
_ISSUE_139_REASONS = {
    "cloud_location_missing": "location_missing",
    "cloud_credentials_missing": "credentials_missing",
    "cloud_user_action_required": "user_action_required",
    "cloud_materialization_failed": "materialization_failed",
    "cloud_allocation_conflict": "allocation_conflict",
}


# --------------------------------------------------------------------------- #
# Setup helpers
# --------------------------------------------------------------------------- #


def _alice(root, *, storage=True):
    alice_hex = provisioning.create_new_participant(root, "Alice")
    if storage:
        cloud_dir = root / "alice-cloud"
        cloud_dir.mkdir()
        provisioning.add_cloud_storage(
            root, alice_hex, protocol="localfolder", url=str(cloud_dir)
        )
    provisioning.create_team(root, alice_hex, TEAM)
    return alice_hex


class _CloudRefusingSession:
    """A live Hub session whose cloud precondition fails."""

    token = "0" * 64

    def __init__(self, reason):
        self.reason = reason

    def ensure_cloud_ready(self):
        # The Hub's 409 body carries no `detail`, so the client's message is
        # the raw envelope. Reproducing that here is the point.
        raise SmallSeaCloudStorageRequired(
            self.reason,
            '{"error":"cloud_storage_required","reason":"%s"}' % self.reason,
        )


def _web(root, participant_hex):
    return TestClient(create_app(str(root), participant_hex))


def _cli(root, participant_hex, *args):
    return CliRunner().invoke(
        cli, ["--root-dir", str(root), "--participant-hex", participant_hex, *args]
    )


# --------------------------------------------------------------------------- #
# The cloud-to-route mapping, independent of any rendering
# --------------------------------------------------------------------------- #


def test_each_typed_cloud_precondition_maps_to_a_distinct_route_reason():
    mapped = [ROUTE_REASON_BY_CLOUD_REASON[cloud] for cloud in _ISSUE_139_REASONS]

    assert mapped == list(_ISSUE_139_REASONS.values())
    # Collapsing any two would send the user to a repair that cannot work.
    assert len(set(mapped)) == len(mapped)


@pytest.mark.parametrize("help_map", [web_module._ROUTE_HELP, cli_module._ROUTE_HELP])
def test_every_mapped_route_reason_has_guidance(help_map):
    assert set(ROUTE_REASON_BY_CLOUD_REASON.values()) <= set(help_map)


@pytest.mark.parametrize("help_map", [web_module._ROUTE_HELP, cli_module._ROUTE_HELP])
def test_missing_location_and_missing_credentials_get_different_guidance(help_map):
    location = help_map["location_missing"]
    credentials = help_map["credentials_missing"]

    assert location != credentials
    # Neither may be the old collapsed advice: an account is already
    # registered in both cases.
    assert location != help_map["storage_not_configured"]
    assert credentials != help_map["storage_not_configured"]
    # Reconciling creates a location by itself; credentials are device-local
    # material that no route operation can supply, so the repair names
    # connecting the account already selected. ("selected" survives as an
    # ordinary adjective, so a bare "select" check would test nothing.)
    assert "econcil" in location
    assert "replacement" not in location
    assert "replacement" not in credentials
    assert "onnect" in credentials
    assert "on this device" in credentials
    assert "econcil" not in credentials


def test_credentials_missing_does_not_offer_the_reconcile_repair():
    assert "location_missing" in web_module._RECONCILABLE_ROUTE_REASONS
    assert "credentials_missing" not in web_module._RECONCILABLE_ROUTE_REASONS
    assert "storage_not_configured" not in web_module._RECONCILABLE_ROUTE_REASONS


# --------------------------------------------------------------------------- #
# Push explains a refused cloud precondition
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cloud_reason, route_reason", _ISSUE_139_REASONS.items())
def test_push_renders_guidance_instead_of_raw_hub_json(
    playground_dir, monkeypatch, cloud_reason, route_reason
):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    monkeypatch.setattr(
        TeamManager,
        "_get_or_open_session",
        lambda self, team, mode="encrypted": _CloudRefusingSession(cloud_reason),
    )

    body = _web(root, alice_hex).post(f"/teams/{TEAM}/push").text

    assert "cloud_storage_required" not in body
    assert route_reason in body
    assert str(escape(web_module._ROUTE_HELP[route_reason])) in body


@pytest.mark.parametrize(
    "cloud_reason, offers_repair",
    [("cloud_location_missing", True), ("cloud_credentials_missing", False)],
)
def test_push_offers_the_repair_only_where_reconciling_helps(
    playground_dir, monkeypatch, cloud_reason, offers_repair
):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    monkeypatch.setattr(
        TeamManager,
        "_get_or_open_session",
        lambda self, team, mode="encrypted": _CloudRefusingSession(cloud_reason),
    )

    body = _web(root, alice_hex).post(f"/teams/{TEAM}/push").text

    assert (f"/teams/{TEAM}/reconcile-route" in body) is offers_repair


# --------------------------------------------------------------------------- #
# Session authorization and current-route state are separate signals
# --------------------------------------------------------------------------- #


def test_an_active_session_and_pending_current_route_render_together(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root, storage=False)
    app = create_app(str(root), alice_hex)
    app.state.manager.set_session(TEAM, "0" * 64)

    body = TestClient(app).get(f"/teams/{TEAM}").text

    assert "Hub session active" in body
    assert "Current Core route pending" in body
    assert "Current Core route published" not in body
    assert "peer routing will fail" not in body
    assert "earlier published route" in body


def test_a_ready_route_reports_the_current_route(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)

    body = _web(root, alice_hex).get(f"/teams/{TEAM}").text

    assert "Current Core route published" in body
    assert "Current Core route pending" not in body


def test_the_sidebar_indicator_says_it_is_a_session_signal(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root, storage=False)

    body = _web(root, alice_hex).get("/").text
    sidebar = body.split('<div id="sidebar-teams">')[1].split("</div>")[0]

    # The dot is labelled for what it is. Storage is unready here, and the
    # sidebar row says nothing about it either way.
    assert 'title="Hub session: none"' in sidebar
    assert "storage" not in sidebar.lower()


# --------------------------------------------------------------------------- #
# CLI guidance for the two split reasons
# --------------------------------------------------------------------------- #


def _pending_route_state(real):
    def _derive(*args, **kwargs):
        return {**real(*args, **kwargs), "route": "pending"}

    return _derive


@pytest.mark.parametrize(
    "cloud_reason, offers_retry",
    [("cloud_location_missing", True), ("cloud_credentials_missing", False)],
)
def test_reconcile_result_offers_retry_only_where_it_can_help(
    playground_dir, monkeypatch, cloud_reason, offers_retry
):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    monkeypatch.setattr(
        TeamManager,
        "_get_or_open_session",
        lambda self, team, mode="encrypted": _CloudRefusingSession(cloud_reason),
    )
    monkeypatch.setattr(
        provisioning,
        "derive_team_join_state",
        _pending_route_state(provisioning.derive_team_join_state),
    )

    body = _web(root, alice_hex).post(f"/teams/{TEAM}/reconcile-route").text

    assert (">Retry</button>" in body) is offers_retry
    if offers_retry:
        retry = body.split('<form hx-post="/teams/')[1].split("</form>")[0]
        assert 'name="cloud_storage_id"' not in retry
        assert 'name="new_location"' not in retry


@pytest.mark.parametrize(
    "cloud_reason, route_reason",
    [
        ("cloud_location_missing", "location_missing"),
        ("cloud_credentials_missing", "credentials_missing"),
    ],
)
def test_cli_reconcile_names_the_split_reason(
    playground_dir, monkeypatch, cloud_reason, route_reason
):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    monkeypatch.setattr(
        TeamManager,
        "_get_or_open_session",
        lambda self, team, mode="encrypted": _CloudRefusingSession(cloud_reason),
    )
    # A ready route would skip the Hub entirely; the repair path is the one
    # that meets the precondition.
    monkeypatch.setattr(
        provisioning,
        "derive_team_join_state",
        _pending_route_state(provisioning.derive_team_join_state),
    )

    result = _cli(root, alice_hex, "reconcile-route", TEAM)

    assert result.exit_code == 0
    assert route_reason in result.stderr
    assert cli_module._ROUTE_HELP[route_reason] in result.stderr


# --------------------------------------------------------------------------- #
# The whole repair-to-push sequence, against MinIO
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def minio(minio_server_gen):
    return minio_server_gen(port=None)


def test_storage_configured_late_is_repaired_and_then_pushes(playground_dir, minio):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root, storage=False)

    hub = SmallSea.SmallSeaBackend(root_dir=str(root), auto_approve_sessions=True)
    hub_app.state.backend = hub
    hub_http = TestClient(hub_app)
    web_app = create_app(str(root), alice_hex)
    web_app.state.manager = TeamManager(root, alice_hex, _http_client=hub_http)
    client = TestClient(web_app)

    # 1. No storage at all: the push names the precondition, not raw JSON.
    body = client.post(f"/teams/{TEAM}/push").text
    assert "cloud_storage_required" not in body
    assert "location_missing" in body
    assert f"/teams/{TEAM}/reconcile-route" in body

    # 2. Register the account the Manager owns, after the fact.
    account = web_app.state.manager.add_cloud_storage(
        protocol="s3",
        url=minio["endpoint"],
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    )

    # 3. Select it and let reconciliation allocate, materialize, and publish.
    body = client.post(
        f"/teams/{TEAM}/reconcile-route", data={"cloud_storage_id": account}
    ).text
    assert "Core route published" in body
    assert "Current Core route published" in body

    # 4. The same push now succeeds against the repaired route.
    body = client.post(f"/teams/{TEAM}/push").text
    assert "Pushed to cloud." in body
