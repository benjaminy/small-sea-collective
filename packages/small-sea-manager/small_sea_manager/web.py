"""FastAPI + Jinja2 + htmx web UI for the Small Sea Manager."""

import asyncio
import base64
import json
import pathlib
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cod_sync.protocol import (
    PublicationIntegrationRequiredError,
    PublicationOutcomeUnresolvedError,
    PublicationRetryableError,
)
from small_sea_client.client import SmallSeaCloudStorageRequired
from small_sea_manager.manager import (
    ROUTE_REASON_BY_CLOUD_REASON,
    TeamManager,
    _CORE_APP,
)

_template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_template_dir)

_NOTETOSELF = "NoteToSelf"

#: What the user should do about a pending route. Every reason is retryable.
_ROUTE_HELP = {
    "storage_not_configured": "Add cloud storage below, then retry.",
    "location_missing": (
        "This device has no cloud location for the team's Core berth yet. "
        "Reconcile the Core route to create one."
    ),
    "credentials_missing": (
        "The selected cloud account has no credentials saved on this device. "
        "Connect it under Cloud Storage below, then retry."
    ),
    "hub_session_unavailable": "Open a session for this team, then retry.",
    "user_action_required": "Your storage provider needs your attention, then retry.",
    "materialization_failed": "Setting up your cloud storage failed. Retry.",
    "allocation_conflict": "Your cloud location changed underneath. Retry.",
    "route_preparation_error": "Route preparation failed. Retry.",
    "storage_choice_required": "Choose which storage account to publish on.",
    "current_device_untrusted": (
        "This device's team key is not trusted, so a route it signs cannot help "
        "your teammates. Finish admission, or use a trusted device."
    ),
    "berth_source_paused": (
        "Two of your devices chose different cloud locations for this team, so "
        "this device has stopped using either one. Review both and choose which "
        "to keep; retrying will not clear it."
    ),
    "berth_source_ambiguous": (
        "This team's Core berth has more than one cloud location on this device "
        "and none of them was chosen. Review the placement question and choose."
    ),
}

#: Route reasons the existing Core-route reconciliation repairs. The two that
#: are missing from it need the user to change something first: registering a
#: storage account, or connecting the selected account's credentials on this
#: device.
_RECONCILABLE_ROUTE_REASONS = frozenset(
    {
        "location_missing",
        "user_action_required",
        "materialization_failed",
        "allocation_conflict",
        "route_preparation_error",
    }
)

#: NoteToSelf push and refresh both need the passthrough session; integration
#: does not, because it reads only local refs and the local database.
_CONNECT_TO_HUB_FIRST = "Connect to Hub above before pushing or refreshing NoteToSelf."


def _conflict_key_text(key) -> str:
    """Render one normalised row key as something a person can match a row on."""

    def part(element):
        if isinstance(element, tuple) and len(element) == 2:
            head, value = element
            if head == "blob":
                return value
            if head == "val":
                return "NULL" if value is None else str(value)
            return f"{head}={part(value)}"
        return str(element)

    return ", ".join(part(element) for element in key)


def _source_outcome_text(outcome) -> tuple[str | None, str | None]:
    """(notice, error) describing what adopting one stored head did."""
    short = outcome.head_sha[:12]
    if outcome.outcome == "integrated":
        return f"Integrated {short} into this device's NoteToSelf history.", None
    if outcome.outcome == "already_contained":
        return f"{short} was already part of this device's history.", None
    if outcome.outcome == "recording_pending":
        return (
            None,
            f"The rows from {short} are adopted and committed to the database, but "
            f"recording the new history in Git did not finish ({outcome.detail}). "
            "Run this again to record it; nothing will be adopted twice.",
        )
    if outcome.outcome == "semantic_conflict":
        return (
            None,
            f"Refused {short}: {outcome.detail}. Nothing was changed, and the "
            "stored history stays available.",
        )
    if outcome.outcome == "constraint_refused":
        return (
            None,
            f"Refused {short}: {outcome.detail} Nothing was changed, and the "
            "stored history stays available.",
        )
    # Cod Sync proves structure, bundle contents and ancestry, not authorship
    # (#190), so a refusal says what this Manager could not read -- never that a
    # sibling device wrote the source.
    return (
        None,
        f"Refused {short}: this Manager cannot read it as NoteToSelf history -- "
        f"{outcome.detail}. Nothing was changed.",
    )


def _conflict_rows(outcome) -> list:
    """The D9 conflicts of one outcome, in the shape the card renders."""
    return [
        {
            "table": conflict.table,
            "key": _conflict_key_text(conflict.key),
            "kind": conflict.kind,
        }
        for conflict in outcome.conflicts
    ]


def _integration_report(result):
    """(notice, error, conflicts) for one run of the integration operation."""
    if result.blocked:
        return None, result.blocked, []
    if not result.outcomes:
        return "No stored NoteToSelf history is waiting to be integrated.", None, []
    notices, errors, conflicts = [], [], []
    for outcome in result.outcomes:
        notice, error = _source_outcome_text(outcome)
        if notice:
            notices.append(notice)
        if error:
            errors.append(error)
        conflicts.extend(_conflict_rows(outcome))
    return (
        " ".join(notices) or None,
        " ".join(errors) or None,
        conflicts,
    )


#: What the inviter is told about the couriered route. These describe local
#: processing only -- none of them claims the teammate's storage is reachable.
_ROUTE_DELIVERY_NOTICE = {
    "imported": "A Core route claim came with it; the route passed setup before it was sent.",
    "missing": "No route claim came with it, so this teammate is not routable yet.",
    "invalid": "The attached route claim did not verify and was discarded.",
    "conflict": "The attached route claim collided with a stored row and was discarded.",
}


def create_app(root_dir: str, participant_hex: str, hub_port: int = 11437) -> FastAPI:
    """Create a configured FastAPI application."""
    app = FastAPI(title="Small Sea Manager")
    app.state.manager = TeamManager(root_dir, participant_hex, hub_port)

    _NTS_TEAM = "NoteToSelf"
    _ENCRYPTED = "encrypted"
    _PASSTHROUGH = "passthrough"

    def _mode_badge(mode: str) -> str | None:
        return "[unsafe]" if mode == _PASSTHROUGH else None

    def _mgr(request: Request) -> TeamManager:
        return request.app.state.manager

    def _hub_connection_ctx(request: Request, error: str = None):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/hub_connection.html",
            {
                "request": request,
                "session_status": mgr.session_state(_NTS_TEAM, _PASSTHROUGH),
                "session_error": error,
                "session_mode_badge": _mode_badge(_PASSTHROUGH),
            },
        )

    def _team_session_ctx(request: Request, team_name: str, error: str = None):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/team_session.html",
            {
                "request": request,
                "team_name": team_name,
                "team_session_status": mgr.session_state(team_name, _ENCRYPTED),
                "session_error": error,
                "team_session_mode_badge": _mode_badge(_ENCRYPTED),
            },
        )

    def _watch_delay(active: bool, *, hub_available: bool = True) -> str:
        if not active:
            return "5s"
        return "0.2s" if hub_available else "5s"

    def _mark_teammate_fields(team: dict[str, Any]) -> list[dict[str, Any]]:
        self_in_team = team.get("self_in_team")
        teammates: list[dict[str, Any]] = []
        for raw_teammate in team["teammates"]:
            teammate = dict(raw_teammate)
            teammate["is_self"] = teammate["id"] == self_in_team
            roles = teammate.get("berth_roles", [])
            teammate["core_role"] = roles[0]["role"] if roles else None
            teammate["can_remove"] = team.get("viewer_is_steward", False) and not teammate["is_self"]
            teammates.append(teammate)
        return teammates

    def _team_detail_context(mgr: TeamManager, team_name: str, *, notice: str = None, error: str = None):
        team = mgr.get_team(team_name)
        if not team.get("joined_locally"):
            return {
                "team_name": team_name,
                "joined_locally": False,
                "teammates": [],
                "invitations": [],
                "admission_events": [],
                "viewer_is_steward": False,
                "sync_status": None,
                "team_session_status": "none",
                "team_session_mode_badge": None,
                "team_notice": notice,
                "team_error": error,
            }
        return {
            "team_name": team_name,
            "joined_locally": True,
            "teammates": _mark_teammate_fields(team),
            "invitations": team["invitations"],
            "admission_events": team["admission_events"],
            "viewer_is_steward": team["viewer_is_steward"],
            "sync_status": mgr.get_team_sync_status(team_name),
            "team_session_status": mgr.session_state(team_name, _ENCRYPTED),
            "team_session_mode_badge": _mode_badge(_ENCRYPTED),
            "admission_watch_delay": _watch_delay(
                mgr.session_state(team_name, _ENCRYPTED) == "active"
            ),
            "core_storage": mgr.core_storage_allocation(team_name),
            "cloud_providers": mgr.list_cloud_storage(),
            "route_help": _ROUTE_HELP,
            "core_route": None,
            "core_route_error": None,
            "offer_reconcile": False,
            "team_notice": notice,
            "team_error": error,
        }

    def _render_team_detail(request: Request, team_name: str, *, notice: str = None, error: str = None):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/team_detail.html",
            {"request": request, **_team_detail_context(mgr, team_name, notice=notice, error=error)},
        )

    def _render_admission_events(request: Request, team_name: str, *, notice: str = None, error: str = None):
        mgr = _mgr(request)
        team = mgr.get_team(team_name)
        return templates.TemplateResponse(
            "fragments/admission_events.html",
            {
                "request": request,
                "team_name": team_name,
                "admission_events": team.get("admission_events", []),
                "viewer_is_steward": team.get("viewer_is_steward", False),
                "notice": notice,
                "error": error,
            },
        )

    def _render_app_sightings(
        request: Request,
        *,
        sightings: list[dict[str, Any]] | None = None,
        notice: str = None,
        error: str = None,
    ):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/app_sightings.html",
            {
                "request": request,
                "sightings": sightings,
                "notice": notice,
                "error": error,
                "session_status": mgr.session_state(_NTS_TEAM, _PASSTHROUGH),
            },
        )

    def _render_note_to_self_sync(
        request: Request,
        *,
        notice: str = None,
        error: str = None,
        conflicts: list[dict[str, Any]] | None = None,
        sidebar_oob: bool = False,
    ):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/note_to_self_sync.html",
            {
                "request": request,
                "session_status": mgr.session_state(_NTS_TEAM, _PASSTHROUGH),
                # Read from refs on every render, so the offer survives a
                # Manager restart and never depends on remembered state.
                "nts_sources": mgr.note_to_self_conflict_status(),
                "nts_notice": notice,
                "nts_error": error,
                "nts_conflicts": conflicts or [],
                "sidebar_oob": sidebar_oob,
                "teams": _teams_with_status(mgr) if sidebar_oob else [],
            },
        )

    def _refresh_app_sightings_after_action(request: Request, notice: str):
        mgr = _mgr(request)
        try:
            sightings = mgr.refresh_app_sightings()
        except Exception as e:
            return _render_app_sightings(
                request,
                notice=notice,
                error=(
                    "Saved locally, but could not refresh sightings. "
                    f"Reconnect to Hub and Refresh. ({e})"
                ),
            )
        return _render_app_sightings(
            request,
            sightings=sightings,
            notice=notice,
            error=getattr(sightings, "cleanup_warning", None),
        )

    # ------------------------------------------------------------------ #
    # Full pages
    # ------------------------------------------------------------------ #

    def _teams_with_status(mgr):
        teams = [t for t in mgr.list_teams() if t["name"] != _NOTETOSELF]
        for t in teams:
            if t.get("joined_locally"):
                t["sync_status"] = mgr.get_team_sync_status(t["name"])
                t["session_status"] = mgr.session_state(t["name"], _ENCRYPTED)
            else:
                t["sync_status"] = None
                t["session_status"] = "none"
        return teams

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "nickname": mgr.get_nickname(),
                "participant_short": mgr.participant_hex[:8],
                "teams": _teams_with_status(mgr),
                "session_status": mgr.session_state(_NTS_TEAM, _PASSTHROUGH),
                "session_error": None,
                "sightings": None,
                "nts_sources": mgr.note_to_self_conflict_status(),
                "nts_notice": None,
                "nts_error": None,
                "nts_conflicts": [],
                "notice": None,
                "error": None,
            },
        )

    # ------------------------------------------------------------------ #
    # Hub connection (NoteToSelf PIN flow)
    # ------------------------------------------------------------------ #

    @app.post("/session/request", response_class=HTMLResponse)
    async def session_request(request: Request):
        mgr = _mgr(request)
        try:
            session, pending_id = mgr.client.start_session(
                mgr.participant_hex, _CORE_APP, _NTS_TEAM, "ManagerUI", mode=_PASSTHROUGH
            )
            if session is not None:
                mgr.set_session(_NTS_TEAM, session.token, mode=_PASSTHROUGH)
            else:
                mgr.set_pending(_NTS_TEAM, pending_id, mode=_PASSTHROUGH)
        except Exception as e:
            return _hub_connection_ctx(request, error=str(e))
        return _hub_connection_ctx(request)

    @app.post("/session/confirm", response_class=HTMLResponse)
    async def session_confirm(request: Request, pin: str = Form(...)):
        mgr = _mgr(request)
        pending_id = mgr.get_pending_id(_NTS_TEAM, _PASSTHROUGH)
        try:
            session = mgr.client.confirm_session(pending_id, pin.strip())
            mgr.set_session(_NTS_TEAM, session.token, mode=_PASSTHROUGH)
        except Exception as e:
            return _hub_connection_ctx(request, error=str(e))
        return _hub_connection_ctx(request)

    @app.post("/session/resend-notification", response_class=HTMLResponse)
    async def session_resend_notification(request: Request):
        mgr = _mgr(request)
        pending_id = mgr.get_pending_id(_NTS_TEAM, _PASSTHROUGH)
        try:
            if pending_id:
                mgr.client.resend_notification(pending_id)
        except Exception as e:
            return _hub_connection_ctx(request, error=str(e))
        return _hub_connection_ctx(request)

    @app.post("/session/close", response_class=HTMLResponse)
    async def session_close(request: Request):
        mgr = _mgr(request)
        mgr.clear_session(_NTS_TEAM, mode=_PASSTHROUGH)
        return _hub_connection_ctx(request)

    # ------------------------------------------------------------------ #
    # NoteToSelf sync
    # ------------------------------------------------------------------ #

    @app.post("/note-to-self/push", response_class=HTMLResponse)
    async def note_to_self_push(request: Request):
        mgr = _mgr(request)
        # Enforced here as well as hidden in the card: a stale page must not be
        # able to open a transport this installation has no session for.
        if mgr.session_state(_NTS_TEAM, _PASSTHROUGH) != "active":
            return _render_note_to_self_sync(request, error=_CONNECT_TO_HUB_FIRST)
        try:
            mgr.push_note_to_self()
        except PublicationIntegrationRequiredError:
            return _render_note_to_self_sync(
                request,
                error=(
                    "Push refused: your stored NoteToSelf history holds changes this "
                    "device does not have. Both are kept locally. Integrate the "
                    "stored history, then push again."
                ),
            )
        except PublicationOutcomeUnresolvedError:
            return _render_note_to_self_sync(
                request,
                error=(
                    "Push outcome unknown: the stored head may or may not have moved. "
                    "Local state is preserved. A later push observes the stored state "
                    "afresh before attempting another write."
                ),
            )
        except PublicationRetryableError:
            return _render_note_to_self_sync(
                request,
                error=(
                    "Push did not finish, but this attempt can no longer change the "
                    "stored head. Push again."
                ),
            )
        except Exception as e:
            return _render_note_to_self_sync(request, error=str(e))
        return _render_note_to_self_sync(request, notice="Pushed NoteToSelf to cloud.")

    @app.post("/note-to-self/refresh", response_class=HTMLResponse)
    async def note_to_self_refresh(request: Request):
        mgr = _mgr(request)
        if mgr.session_state(_NTS_TEAM, _PASSTHROUGH) != "active":
            return _render_note_to_self_sync(request, error=_CONNECT_TO_HUB_FIRST)
        try:
            result = mgr.refresh_note_to_self()
        except Exception as e:
            return _render_note_to_self_sync(request, error=str(e))
        outcome = result["integration"]
        notice, error = _source_outcome_text(outcome)
        return _render_note_to_self_sync(
            request,
            notice=notice,
            error=error,
            conflicts=_conflict_rows(outcome),
            sidebar_oob=True,
        )

    @app.post("/note-to-self/integrate", response_class=HTMLResponse)
    async def note_to_self_integrate(request: Request):
        # Deliberately available with no Hub session: integration reads only
        # local refs and the local database.
        mgr = _mgr(request)
        try:
            result = mgr.integrate_note_to_self()
        except Exception as e:
            return _render_note_to_self_sync(request, error=str(e))
        notice, error, conflicts = _integration_report(result)
        return _render_note_to_self_sync(
            request,
            notice=notice,
            error=error,
            conflicts=conflicts,
            sidebar_oob=result.adopted_anything,
        )

    # ------------------------------------------------------------------ #
    # App-bootstrap sightings
    # ------------------------------------------------------------------ #

    @app.post("/app-sightings/refresh", response_class=HTMLResponse)
    async def app_sightings_refresh(request: Request):
        mgr = _mgr(request)
        if mgr.session_state(_NTS_TEAM, _PASSTHROUGH) != "active":
            return _render_app_sightings(
                request,
                error="Connect to Hub to refresh sightings.",
            )
        try:
            sightings = mgr.refresh_app_sightings()
        except Exception as e:
            return _render_app_sightings(request, error=str(e))
        return _render_app_sightings(
            request,
            sightings=sightings,
            error=getattr(sightings, "cleanup_warning", None),
        )

    @app.post("/app-sightings/register", response_class=HTMLResponse)
    async def app_sightings_register(request: Request, app_name: str = Form(...)):
        mgr = _mgr(request)
        try:
            mgr.register_app_for_participant(app_name)
        except Exception as e:
            return _render_app_sightings(request, error=str(e))
        return _refresh_app_sightings_after_action(
            request,
            f"Registered {app_name} for this participant.",
        )

    @app.post("/app-sightings/activate", response_class=HTMLResponse)
    async def app_sightings_activate(
        request: Request,
        team_name: str = Form(...),
        app_name: str = Form(...),
    ):
        mgr = _mgr(request)
        try:
            mgr.activate_app_for_team(team_name, app_name)
        except Exception as e:
            return _render_app_sightings(request, error=str(e))
        return _refresh_app_sightings_after_action(
            request,
            f"Activated {app_name} for {team_name}.",
        )

    @app.post("/app-sightings/dismiss-participant", response_class=HTMLResponse)
    async def app_sightings_dismiss_participant(
        request: Request,
        app_name: str = Form(...),
    ):
        mgr = _mgr(request)
        try:
            mgr.dismiss_participant_app_sighting(app_name)
        except Exception as e:
            return _render_app_sightings(request, error=str(e))
        return _refresh_app_sightings_after_action(
            request,
            f"Dismissed participant prompt for {app_name}.",
        )

    @app.post("/app-sightings/dismiss-team", response_class=HTMLResponse)
    async def app_sightings_dismiss_team(
        request: Request,
        team_name: str = Form(...),
        app_name: str = Form(...),
    ):
        mgr = _mgr(request)
        try:
            mgr.dismiss_team_app_sighting(team_name, app_name)
        except Exception as e:
            return _render_app_sightings(request, error=str(e))
        return _refresh_app_sightings_after_action(
            request,
            f"Dismissed {team_name} prompt for {app_name}.",
        )

    # ------------------------------------------------------------------ #
    # Teams
    # ------------------------------------------------------------------ #

    @app.post("/teams", response_class=HTMLResponse)
    async def create_team(request: Request, team_name: str = Form(...)):
        mgr = _mgr(request)
        try:
            mgr.create_team(team_name)
            error = None
        except Exception as e:
            error = str(e)
        return templates.TemplateResponse(
            "fragments/sidebar_teams.html",
            {"request": request, "teams": _teams_with_status(mgr), "error": error},
        )

    # ------------------------------------------------------------------ #
    # Team detail
    # ------------------------------------------------------------------ #

    @app.get("/teams/{team_name}", response_class=HTMLResponse)
    async def team_detail(request: Request, team_name: str):
        return _render_team_detail(request, team_name)

    # ------------------------------------------------------------------ #
    # Team sessions (PIN flow per team)
    # ------------------------------------------------------------------ #

    @app.post("/teams/{team_name}/session/request", response_class=HTMLResponse)
    async def team_session_request(request: Request, team_name: str):
        mgr = _mgr(request)
        try:
            session, pending_id = mgr.client.start_session(
                mgr.participant_hex, _CORE_APP, team_name, "ManagerUI", mode=_ENCRYPTED
            )
            if session is not None:
                mgr.set_session(team_name, session.token, mode=_ENCRYPTED)
            else:
                mgr.set_pending(team_name, pending_id, mode=_ENCRYPTED)
        except Exception as e:
            return _team_session_ctx(request, team_name, error=str(e))
        return _team_session_ctx(request, team_name)

    @app.post("/teams/{team_name}/session/confirm", response_class=HTMLResponse)
    async def team_session_confirm(
        request: Request, team_name: str, pin: str = Form(...)
    ):
        mgr = _mgr(request)
        pending_id = mgr.get_pending_id(team_name, _ENCRYPTED)
        try:
            session = mgr.client.confirm_session(pending_id, pin.strip())
            mgr.set_session(team_name, session.token, mode=_ENCRYPTED)
        except Exception as e:
            return _team_session_ctx(request, team_name, error=str(e))
        return _team_session_ctx(request, team_name)

    @app.post("/teams/{team_name}/session/resend-notification", response_class=HTMLResponse)
    async def team_session_resend(request: Request, team_name: str):
        mgr = _mgr(request)
        pending_id = mgr.get_pending_id(team_name, _ENCRYPTED)
        try:
            if pending_id:
                mgr.client.resend_notification(pending_id)
        except Exception as e:
            return _team_session_ctx(request, team_name, error=str(e))
        return _team_session_ctx(request, team_name)

    @app.post("/teams/{team_name}/session/close", response_class=HTMLResponse)
    async def team_session_close(request: Request, team_name: str):
        mgr = _mgr(request)
        mgr.clear_session(team_name, mode=_ENCRYPTED)
        return _team_session_ctx(request, team_name)

    # ------------------------------------------------------------------ #
    # Sync
    # ------------------------------------------------------------------ #

    @app.get("/teams/{team_name}/sync-status", response_class=HTMLResponse)
    async def team_sync_status(request: Request, team_name: str):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/sync_badge.html",
            {"request": request, "team_name": team_name,
             "status": mgr.get_team_sync_status(team_name)},
        )

    @app.post("/teams/{team_name}/push", response_class=HTMLResponse)
    async def push_team(request: Request, team_name: str):
        mgr = _mgr(request)
        storage_reason = None
        try:
            outcome = mgr.push_team(team_name)
            notice = (
                "Already published."
                if outcome == "already_present"
                else "Pushed to cloud."
            )
            error = None
        except SmallSeaCloudStorageRequired as exc:
            # A live session says nothing about current-route state, and the
            # Hub's 409 body carries no `detail`, so its raw JSON envelope is
            # all `str(exc)` would show. Speak the same route reasons the
            # Core-storage section does, and hand the repair to it.
            notice = None
            storage_reason = ROUTE_REASON_BY_CLOUD_REASON.get(
                exc.reason, "route_preparation_error"
            )
            error = (
                f"Push blocked: this team's Core storage is not ready "
                f"({storage_reason}). {_ROUTE_HELP[storage_reason]}"
            )
        except PublicationIntegrationRequiredError:
            # Manager has no integration operation yet (#185, #48), so this
            # says what is true and offers no action that does not exist. The
            # competing head is parked, so nothing is lost by waiting.
            notice = None
            error = (
                "Push refused: your cloud Core chain holds changes this installation "
                "does not have. Both histories are kept locally; this installation "
                "cannot combine them yet. The local commit remains unpublished."
            )
        except PublicationOutcomeUnresolvedError:
            notice = None
            error = (
                "Push outcome unknown: the cloud head may or may not have moved. "
                "The local commit is preserved. A later push will observe the cloud "
                "state afresh before attempting another write."
            )
        except PublicationRetryableError:
            notice = None
            error = (
                "Push did not finish, but this attempt can no longer change the "
                "cloud head. Push again."
            )
        except Exception as e:
            notice = None
            error = str(e)
        return templates.TemplateResponse(
            "fragments/sync_result.html",
            {
                "request": request,
                "team_name": team_name,
                "notice": notice,
                "error": error,
                "offer_reconcile": storage_reason in _RECONCILABLE_ROUTE_REASONS,
            },
        )

    # ------------------------------------------------------------------ #
    # Invitations
    # ------------------------------------------------------------------ #

    @app.post("/teams/{team_name}/invitations", response_class=HTMLResponse)
    async def create_invitation(
        request: Request,
        team_name: str,
        invitee_label: str = Form(""),
        role: str = Form("steward"),
    ):
        mgr = _mgr(request)
        try:
            token = mgr.create_invitation(
                team_name,
                invitee_label=invitee_label or None,
                role=role,
            )
            error = None
        except Exception as e:
            token = None
            error = str(e)
        return templates.TemplateResponse(
            "fragments/invitation_token.html",
            {
                "request": request,
                "team_name": team_name,
                "token": token,
                "error": error,
                "team": _mgr(request).get_team(team_name),
            },
        )

    @app.post(
        "/teams/{team_name}/invitations/{inv_id}/revoke", response_class=HTMLResponse
    )
    async def revoke_invitation(request: Request, team_name: str, inv_id: str):
        mgr = _mgr(request)
        try:
            mgr.revoke_invitation(team_name, inv_id)
            notice = "Invitation revoked."
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        return _render_team_detail(request, team_name, notice=notice, error=error)

    @app.post(
        "/teams/{team_name}/invitations/{inv_id}/approve", response_class=HTMLResponse
    )
    async def approve_invitation(request: Request, team_name: str, inv_id: str):
        mgr = _mgr(request)
        try:
            mgr.endorse_admission(team_name, inv_id)
            notice = "Endorsement recorded."
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        return _render_team_detail(request, team_name, notice=notice, error=error)

    @app.post(
        "/teams/{team_name}/invitations/{inv_id}/finalize", response_class=HTMLResponse
    )
    async def finalize_invitation(request: Request, team_name: str, inv_id: str):
        mgr = _mgr(request)
        try:
            mgr.finalize_admission(team_name, inv_id)
            notice = "Admission finalized."
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        return _render_team_detail(request, team_name, notice=notice, error=error)

    # ------------------------------------------------------------------ #
    # Cloud storage
    # ------------------------------------------------------------------ #

    def _cloud_storage_fragment(request, error=None):
        providers = _mgr(request).list_cloud_storage()
        return templates.TemplateResponse(
            "fragments/cloud_storage.html",
            {"request": request, "providers": providers, "error": error},
        )

    @app.get("/cloud-storage", response_class=HTMLResponse)
    async def cloud_storage(request: Request):
        return _cloud_storage_fragment(request)

    @app.post("/cloud-storage", response_class=HTMLResponse)
    async def add_cloud_storage(
        request: Request,
        protocol: str = Form(...),
        url: str = Form(...),
        access_key: str = Form(""),
        secret_key: str = Form(""),
    ):
        mgr = _mgr(request)
        try:
            mgr.add_cloud_storage(
                protocol=protocol,
                url=url.strip(),
                access_key=access_key.strip() or None,
                secret_key=secret_key.strip() or None,
            )
            error = None
        except Exception as e:
            error = str(e)
        return _cloud_storage_fragment(request, error=error)

    @app.post("/cloud-storage/{storage_id}/remove", response_class=HTMLResponse)
    async def remove_cloud_storage(request: Request, storage_id: str):
        mgr = _mgr(request)
        try:
            mgr.remove_cloud_storage(storage_id)
            error = None
        except Exception as e:
            error = str(e)
        return _cloud_storage_fragment(request, error=error)

    @app.post("/cloud-storage/{storage_id}/connect", response_class=HTMLResponse)
    async def connect_cloud_storage(
        request: Request,
        storage_id: str,
        access_key: str = Form(""),
        secret_key: str = Form(""),
    ):
        # First connection and replacement are the same operation: whatever is
        # submitted becomes this device's credentials for that account.
        mgr = _mgr(request)
        try:
            mgr.connect_cloud_storage_credentials(
                storage_id,
                access_key=access_key.strip() or None,
                secret_key=secret_key.strip() or None,
            )
            error = None
        except Exception as e:
            error = str(e)
        return _cloud_storage_fragment(request, error=error)

    @app.post("/cloud-storage/{storage_id}/disconnect", response_class=HTMLResponse)
    async def disconnect_cloud_storage(request: Request, storage_id: str):
        mgr = _mgr(request)
        try:
            mgr.disconnect_cloud_storage_credentials(storage_id)
            error = None
        except Exception as e:
            error = str(e)
        return _cloud_storage_fragment(request, error=error)

    # ------------------------------------------------------------------ #
    # Accept invitation (invitee side)
    # ------------------------------------------------------------------ #

    def _acceptance_fragment(request, mgr, team_name, report, error=None):
        # Pass updated teams list so acceptance_token.html can OOB-update #sidebar-teams
        return templates.TemplateResponse(
            "fragments/acceptance_token.html",
            {
                "request": request,
                "team_name": team_name,
                "report": report,
                "acceptance_token": (report or {}).get("acceptance_token"),
                "route_help": _ROUTE_HELP,
                "error": error,
                "teams": _teams_with_status(mgr) if report else [],
            },
        )

    @app.post("/accept-invitation", response_class=HTMLResponse)
    async def accept_invitation(request: Request, invitation_token: str = Form(...)):
        mgr = _mgr(request)
        team_name = None
        try:
            token = invitation_token.strip()
            team_name = json.loads(base64.b64decode(token))["team_name"]
            report = mgr.accept_invitation(token)
            if report["acceptance"] == "exportable":
                report = mgr.export_admission_acceptance(team_name)
            error = None
        except Exception as e:
            report = None
            error = str(e)
        return _acceptance_fragment(request, mgr, team_name, report, error=error)

    @app.post("/teams/{team_name}/prepare-route", response_class=HTMLResponse)
    async def prepare_route(request: Request, team_name: str):
        mgr = _mgr(request)
        try:
            report = mgr.prepare_team_route(team_name)
            if report["acceptance"] == "exportable":
                report = mgr.export_admission_acceptance(team_name)
            error = None
        except Exception as e:
            report = None
            error = str(e)
        return _acceptance_fragment(request, mgr, team_name, report, error=error)

    def _core_storage_fragment(request, team_name, *, report=None, error=None):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/core_storage.html",
            {
                "request": request,
                "team_name": team_name,
                "core_storage": mgr.core_storage_allocation(team_name),
                "cloud_providers": mgr.list_cloud_storage(),
                "route_help": _ROUTE_HELP,
                "core_route": report,
                "core_route_error": error,
                "offer_reconcile": (
                    report is not None
                    and report.get("route_reason") in _RECONCILABLE_ROUTE_REASONS
                ),
            },
        )

    @app.post("/teams/{team_name}/reconcile-route", response_class=HTMLResponse)
    async def reconcile_route(
        request: Request,
        team_name: str,
        cloud_storage_id: str = Form(""),
        new_location: bool = Form(False),
    ):
        """Reconcile this device's Core storage route for one team.

        Only a registered account ID and a boolean intent come from the form.
        A caller-supplied location is deliberately not accepted here.
        """
        mgr = _mgr(request)
        try:
            report = mgr.reconcile_team_route(
                team_name,
                cloud_storage_id=cloud_storage_id.strip() or None,
                new_location=new_location,
            )
            error = None
        except ValueError as e:
            report = None
            error = str(e)
        return _core_storage_fragment(request, team_name, report=report, error=error)

    @app.post("/teams/{team_name}/complete-acceptance", response_class=HTMLResponse)
    async def complete_acceptance(
        request: Request, team_name: str, acceptance_token: str = Form(...)
    ):
        mgr = _mgr(request)
        try:
            result = mgr.complete_invitation_acceptance(team_name, acceptance_token)
            notice = "Acceptance recorded. " + _ROUTE_DELIVERY_NOTICE[
                result["route_delivery"]
            ]
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        return _render_team_detail(request, team_name, notice=notice, error=error)

    @app.post("/teams/{team_name}/teammates/{teammate_id}/remove", response_class=HTMLResponse)
    async def remove_teammate(request: Request, team_name: str, teammate_id: str):
        mgr = _mgr(request)
        try:
            mgr.remove_teammate(team_name, teammate_id)
            notice = "Teammate excluded and team sender key rotated."
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        return _render_team_detail(request, team_name, notice=notice, error=error)

    @app.post("/teams/{team_name}/admission-events/{event_type}/{artifact_id}/dismiss", response_class=HTMLResponse)
    async def dismiss_admission_event(
        request: Request, team_name: str, event_type: str, artifact_id: str
    ):
        mgr = _mgr(request)
        try:
            mgr.dismiss_admission_event(team_name, event_type, artifact_id)
            notice = "Admission prompt dismissed."
            error = None
        except ValueError as e:
            notice = None
            error = f"Invalid admission event identity: {e}"
        except Exception as e:
            notice = None
            error = str(e)
        return _render_admission_events(request, team_name, notice=notice, error=error)

    @app.get("/teams/{team_name}/admission-events/watch", response_class=HTMLResponse)
    async def watch_admission_events(request: Request, team_name: str):
        mgr = _mgr(request)
        active = mgr.session_state(team_name, _ENCRYPTED) == "active"
        hub_available = True
        if active:
            hub_available = await asyncio.to_thread(
                mgr.wait_for_team_admission_signal,
                team_name,
                15,
            )
        team = mgr.get_team(team_name)
        return templates.TemplateResponse(
            "fragments/admission_events_watch.html",
            {
                "request": request,
                "team_name": team_name,
                "admission_events": team.get("admission_events", []),
                "viewer_is_steward": team.get("viewer_is_steward", False),
                "watch_delay": _watch_delay(active, hub_available=hub_available),
            },
        )

    return app
