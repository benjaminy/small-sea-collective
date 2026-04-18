"""FastAPI + Jinja2 + htmx web UI for the Small Sea Manager."""

import asyncio
import pathlib
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from small_sea_manager.manager import TeamManager, _CORE_APP

_template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_template_dir)

_NOTETOSELF = "NoteToSelf"


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

    def _mark_member_fields(team: dict[str, Any]) -> list[dict[str, Any]]:
        self_in_team = team.get("self_in_team")
        members: list[dict[str, Any]] = []
        for raw_member in team["members"]:
            member = dict(raw_member)
            member["is_self"] = member["id"] == self_in_team
            roles = member.get("berth_roles", [])
            member["core_role"] = roles[0]["role"] if roles else None
            member["can_remove"] = team.get("viewer_is_admin", False) and not member["is_self"]
            members.append(member)
        return members

    def _team_detail_context(mgr: TeamManager, team_name: str, *, notice: str = None, error: str = None):
        team = mgr.get_team(team_name)
        if not team.get("joined_locally"):
            return {
                "team_name": team_name,
                "joined_locally": False,
                "members": [],
                "invitations": [],
                "admission_events": [],
                "viewer_is_admin": False,
                "sync_status": None,
                "team_session_status": "none",
                "team_session_mode_badge": None,
                "team_notice": notice,
                "team_error": error,
            }
        return {
            "team_name": team_name,
            "joined_locally": True,
            "members": _mark_member_fields(team),
            "invitations": team["invitations"],
            "admission_events": team["admission_events"],
            "viewer_is_admin": team["viewer_is_admin"],
            "sync_status": mgr.get_team_sync_status(team_name),
            "team_session_status": mgr.session_state(team_name, _ENCRYPTED),
            "team_session_mode_badge": _mode_badge(_ENCRYPTED),
            "admission_watch_delay": _watch_delay(
                mgr.session_state(team_name, _ENCRYPTED) == "active"
            ),
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
                "viewer_is_admin": team.get("viewer_is_admin", False),
                "notice": notice,
                "error": error,
            },
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
        try:
            mgr.push_team(team_name)
            notice = "Pushed to cloud."
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        return templates.TemplateResponse(
            "fragments/sync_result.html",
            {"request": request, "team_name": team_name, "notice": notice, "error": error},
        )

    # ------------------------------------------------------------------ #
    # Invitations
    # ------------------------------------------------------------------ #

    @app.post("/teams/{team_name}/invitations", response_class=HTMLResponse)
    async def create_invitation(
        request: Request,
        team_name: str,
        invitee_label: str = Form(""),
        role: str = Form("admin"),
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

    # ------------------------------------------------------------------ #
    # Accept invitation (invitee side)
    # ------------------------------------------------------------------ #

    @app.post("/accept-invitation", response_class=HTMLResponse)
    async def accept_invitation(request: Request, invitation_token: str = Form(...)):
        mgr = _mgr(request)
        try:
            acceptance_token = mgr.accept_invitation(invitation_token.strip())
            error = None
        except Exception as e:
            acceptance_token = None
            error = str(e)
        # Pass updated teams list so acceptance_token.html can OOB-update #sidebar-teams
        teams = _teams_with_status(mgr) if acceptance_token else []
        return templates.TemplateResponse(
            "fragments/acceptance_token.html",
            {
                "request": request,
                "acceptance_token": acceptance_token,
                "error": error,
                "teams": teams,
            },
        )

    @app.post("/teams/{team_name}/complete-acceptance", response_class=HTMLResponse)
    async def complete_acceptance(
        request: Request, team_name: str, acceptance_token: str = Form(...)
    ):
        mgr = _mgr(request)
        try:
            mgr.complete_invitation_acceptance(team_name, acceptance_token)
            notice = "Acceptance complete — new member added."
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        return _render_team_detail(request, team_name, notice=notice, error=error)

    @app.post("/teams/{team_name}/members/{member_id}/remove", response_class=HTMLResponse)
    async def remove_member(request: Request, team_name: str, member_id: str):
        mgr = _mgr(request)
        try:
            mgr.remove_member(team_name, member_id)
            notice = "Member excluded and team sender key rotated."
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
                "viewer_is_admin": team.get("viewer_is_admin", False),
                "watch_delay": _watch_delay(active, hub_available=hub_available),
            },
        )

    return app
