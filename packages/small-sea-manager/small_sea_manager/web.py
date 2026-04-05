"""FastAPI + Jinja2 + htmx web UI for the Small Sea Manager."""

import pathlib

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from small_sea_manager.manager import TeamManager

_template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_template_dir)

_NOTETOSELF = "NoteToSelf"


def create_app(root_dir: str, participant_hex: str, hub_port: int = 11437) -> FastAPI:
    """Create a configured FastAPI application."""
    app = FastAPI(title="Small Sea Manager")
    app.state.manager = TeamManager(root_dir, participant_hex, hub_port)

    _NTS = ("SmallSeaCollectiveCore", "NoteToSelf")  # the Manager's primary session scope
    _CORE_APP = "SmallSeaCollectiveCore"  # app name used for all team sessions

    def _mgr(request: Request) -> TeamManager:
        return request.app.state.manager

    def _hub_connection_ctx(request: Request, error: str = None):
        mgr = _mgr(request)
        app_name, team_name = _NTS
        return templates.TemplateResponse(
            "fragments/hub_connection.html",
            {
                "request": request,
                "session_status": mgr.session_state(app_name, team_name),
                "session_error": error,
            },
        )

    def _team_session_ctx(request: Request, team_name: str, error: str = None):
        mgr = _mgr(request)
        return templates.TemplateResponse(
            "fragments/team_session.html",
            {
                "request": request,
                "team_name": team_name,
                "team_session_status": mgr.session_state(_CORE_APP, team_name),
                "session_error": error,
            },
        )

    # ------------------------------------------------------------------ #
    # Full pages
    # ------------------------------------------------------------------ #

    def _teams_with_status(mgr):
        teams = [t for t in mgr.list_teams() if t["name"] != _NOTETOSELF]
        for t in teams:
            t["sync_status"] = mgr.get_team_sync_status(t["name"])
            t["session_status"] = mgr.session_state(_CORE_APP, t["name"])
        return teams

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        mgr = _mgr(request)
        app_name, team_name = _NTS
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "nickname": mgr.get_nickname(),
                "participant_short": mgr.participant_hex[:8],
                "teams": _teams_with_status(mgr),
                "session_status": mgr.session_state(app_name, team_name),
                "session_error": None,
            },
        )

    # ------------------------------------------------------------------ #
    # Hub connection (NoteToSelf PIN flow)
    # ------------------------------------------------------------------ #

    @app.post("/session/request", response_class=HTMLResponse)
    async def session_request(request: Request):
        mgr = _mgr(request)
        app_name, team_name = _NTS
        try:
            session, pending_id = mgr.client.start_session(
                mgr.participant_hex, app_name, team_name, "ManagerUI"
            )
            if session is not None:
                mgr.set_session(app_name, team_name, session.token)
            else:
                mgr.set_pending(app_name, team_name, pending_id)
        except Exception as e:
            return _hub_connection_ctx(request, error=str(e))
        return _hub_connection_ctx(request)

    @app.post("/session/confirm", response_class=HTMLResponse)
    async def session_confirm(request: Request, pin: str = Form(...)):
        mgr = _mgr(request)
        app_name, team_name = _NTS
        pending_id = mgr.get_pending_id(app_name, team_name)
        try:
            session = mgr.client.confirm_session(pending_id, pin.strip())
            mgr.set_session(app_name, team_name, session.token)
        except Exception as e:
            return _hub_connection_ctx(request, error=str(e))
        return _hub_connection_ctx(request)

    @app.post("/session/resend-notification", response_class=HTMLResponse)
    async def session_resend_notification(request: Request):
        mgr = _mgr(request)
        app_name, team_name = _NTS
        pending_id = mgr.get_pending_id(app_name, team_name)
        try:
            if pending_id:
                mgr.client.resend_notification(pending_id)
        except Exception as e:
            return _hub_connection_ctx(request, error=str(e))
        return _hub_connection_ctx(request)

    @app.post("/session/close", response_class=HTMLResponse)
    async def session_close(request: Request):
        mgr = _mgr(request)
        mgr.clear_session(*_NTS)
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
        mgr = _mgr(request)
        all_teams = mgr.list_teams()
        self_in_team = next(
            (t["self_in_team"] for t in all_teams if t["name"] == team_name), None
        )
        members = mgr.list_members(team_name)
        for m in members:
            m["is_self"] = m["id"] == self_in_team
            roles = m.get("berth_roles", [])
            m["core_role"] = roles[0]["role"] if roles else None
        return templates.TemplateResponse(
            "fragments/team_detail.html",
            {
                "request": request,
                "team_name": team_name,
                "members": members,
                "invitations": mgr.list_invitations(team_name),
                "sync_status": mgr.get_team_sync_status(team_name),
                "team_session_status": mgr.session_state(_CORE_APP, team_name),
            },
        )

    # ------------------------------------------------------------------ #
    # Team sessions (PIN flow per team)
    # ------------------------------------------------------------------ #

    @app.post("/teams/{team_name}/session/request", response_class=HTMLResponse)
    async def team_session_request(request: Request, team_name: str):
        mgr = _mgr(request)
        try:
            session, pending_id = mgr.client.start_session(
                mgr.participant_hex, _CORE_APP, team_name, "ManagerUI"
            )
            if session is not None:
                mgr.set_session(_CORE_APP, team_name, session.token)
            else:
                mgr.set_pending(_CORE_APP, team_name, pending_id)
        except Exception as e:
            return _team_session_ctx(request, team_name, error=str(e))
        return _team_session_ctx(request, team_name)

    @app.post("/teams/{team_name}/session/confirm", response_class=HTMLResponse)
    async def team_session_confirm(
        request: Request, team_name: str, pin: str = Form(...)
    ):
        mgr = _mgr(request)
        pending_id = mgr.get_pending_id(_CORE_APP, team_name)
        try:
            session = mgr.client.confirm_session(pending_id, pin.strip())
            mgr.set_session(_CORE_APP, team_name, session.token)
        except Exception as e:
            return _team_session_ctx(request, team_name, error=str(e))
        return _team_session_ctx(request, team_name)

    @app.post("/teams/{team_name}/session/resend-notification", response_class=HTMLResponse)
    async def team_session_resend(request: Request, team_name: str):
        mgr = _mgr(request)
        pending_id = mgr.get_pending_id(_CORE_APP, team_name)
        try:
            if pending_id:
                mgr.client.resend_notification(pending_id)
        except Exception as e:
            return _team_session_ctx(request, team_name, error=str(e))
        return _team_session_ctx(request, team_name)

    @app.post("/teams/{team_name}/session/close", response_class=HTMLResponse)
    async def team_session_close(request: Request, team_name: str):
        mgr = _mgr(request)
        mgr.clear_session(_CORE_APP, team_name)
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
            {"request": request, "team_name": team_name, "token": token, "error": error},
        )

    @app.post(
        "/teams/{team_name}/invitations/{inv_id}/revoke", response_class=HTMLResponse
    )
    async def revoke_invitation(request: Request, team_name: str, inv_id: str):
        mgr = _mgr(request)
        try:
            mgr.revoke_invitation(team_name, inv_id)
            error = None
        except Exception as e:
            error = str(e)
        return templates.TemplateResponse(
            "fragments/invitations.html",
            {
                "request": request,
                "team_name": team_name,
                "invitations": mgr.list_invitations(team_name),
                "error": error,
            },
        )

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
        all_teams = mgr.list_teams()
        self_in_team = next(
            (t["self_in_team"] for t in all_teams if t["name"] == team_name), None
        )
        members = mgr.list_members(team_name)
        for m in members:
            m["is_self"] = m["id"] == self_in_team
            roles = m.get("berth_roles", [])
            m["core_role"] = roles[0]["role"] if roles else None
        return templates.TemplateResponse(
            "fragments/members.html",
            {
                "request": request,
                "team_name": team_name,
                "members": members,
                "notice": notice,
                "error": error,
            },
        )

    return app
