"""FastAPI + Jinja2 + htmx web UI for the Small Sea Manager."""

import pathlib

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from small_sea_manager.manager import TeamManager

_template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_template_dir)

_NOTETOSELF = "NoteToSelf"


def create_app(root_dir: str, participant_hex: str, hub_port: int = 11437) -> FastAPI:
    """Create a configured FastAPI application."""
    app = FastAPI(title="Small Sea Manager")
    app.state.manager = TeamManager(root_dir, participant_hex, hub_port)
    app.state.nts_session_token = None  # NoteToSelf session token, if established

    def _mgr(request: Request) -> TeamManager:
        return request.app.state.manager

    def _session_card_ctx(request: Request, status: str, pending_id: str = None, error: str = None):
        return templates.TemplateResponse(
            "fragments/session_card.html",
            {"request": request, "session_status": status,
             "session_pending_id": pending_id, "session_error": error},
        )

    # ------------------------------------------------------------------ #
    # Full pages
    # ------------------------------------------------------------------ #

    def _teams_with_status(mgr):
        teams = [t for t in mgr.list_teams() if t["name"] != _NOTETOSELF]
        for t in teams:
            t["sync_status"] = mgr.get_team_sync_status(t["name"])
        return teams

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        mgr = _mgr(request)
        nickname = mgr.get_nickname()
        participant_short = mgr.participant_hex[:8]
        teams = _teams_with_status(mgr)
        session_status = "active" if request.app.state.nts_session_token else "none"
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "nickname": nickname,
                "participant_short": participant_short,
                "teams": teams,
                "session_status": session_status,
                "session_pending_id": None,
                "session_error": None,
            },
        )

    # ------------------------------------------------------------------ #
    # Hub session (PIN flow)
    # ------------------------------------------------------------------ #

    @app.post("/session/request", response_class=HTMLResponse)
    async def session_request(request: Request):
        mgr = _mgr(request)
        try:
            session, pending_id = mgr.client.start_session(
                mgr.participant_hex, "SmallSeaCollectiveCore", "NoteToSelf", "ManagerUI"
            )
            if session is not None:
                request.app.state.nts_session_token = session.token
                return _session_card_ctx(request, "active")
            return _session_card_ctx(request, "pending", pending_id=pending_id)
        except Exception as e:
            return _session_card_ctx(request, "none", error=str(e))

    @app.post("/session/confirm", response_class=HTMLResponse)
    async def session_confirm(
        request: Request,
        pending_id: str = Form(...),
        pin: str = Form(...),
    ):
        mgr = _mgr(request)
        try:
            session = mgr.client.confirm_session(pending_id, pin.strip())
            request.app.state.nts_session_token = session.token
            return _session_card_ctx(request, "active")
        except Exception as e:
            return _session_card_ctx(request, "pending", pending_id=pending_id, error=str(e))

    @app.post("/session/resend-notification", response_class=HTMLResponse)
    async def session_resend_notification(
        request: Request,
        pending_id: str = Form(...),
    ):
        mgr = _mgr(request)
        try:
            mgr.client.resend_notification(pending_id)
            return _session_card_ctx(request, "pending", pending_id=pending_id)
        except Exception as e:
            return _session_card_ctx(request, "pending", pending_id=pending_id, error=str(e))

    @app.post("/session/close", response_class=HTMLResponse)
    async def session_close(request: Request):
        request.app.state.nts_session_token = None
        return _session_card_ctx(request, "none")

    # ------------------------------------------------------------------ #
    # Team list management (htmx fragments)
    # ------------------------------------------------------------------ #

    @app.post("/teams", response_class=HTMLResponse)
    async def create_team(request: Request, team_name: str = Form(...)):
        mgr = _mgr(request)
        try:
            mgr.create_team(team_name)
            error = None
        except Exception as e:
            error = str(e)
        teams = _teams_with_status(mgr)
        return templates.TemplateResponse(
            "fragments/teams_section.html",
            {"request": request, "teams": teams, "error": error},
        )

    # ------------------------------------------------------------------ #
    # Team detail (htmx fragment into #team-detail)
    # ------------------------------------------------------------------ #

    @app.get("/teams/{team_name}", response_class=HTMLResponse)
    async def team_detail(request: Request, team_name: str):
        mgr = _mgr(request)
        # Annotate each member with whether they are "self"
        all_teams = mgr.list_teams()
        self_in_team = next(
            (t["self_in_team"] for t in all_teams if t["name"] == team_name), None
        )
        members = mgr.list_members(team_name)
        for m in members:
            m["is_self"] = m["id"] == self_in_team
            roles = m.get("station_roles", [])
            m["core_role"] = roles[0]["role"] if roles else None
        invitations = mgr.list_invitations(team_name)
        sync_status = mgr.get_team_sync_status(team_name)
        return templates.TemplateResponse(
            "fragments/team_detail.html",
            {
                "request": request,
                "team_name": team_name,
                "members": members,
                "invitations": invitations,
                "sync_status": sync_status,
            },
        )

    # ------------------------------------------------------------------ #
    # Sync
    # ------------------------------------------------------------------ #

    @app.get("/teams/{team_name}/sync-status", response_class=HTMLResponse)
    async def team_sync_status(request: Request, team_name: str):
        mgr = _mgr(request)
        status = mgr.get_team_sync_status(team_name)
        return templates.TemplateResponse(
            "fragments/sync_badge.html",
            {"request": request, "team_name": team_name, "status": status},
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
            },
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
        invitations = mgr.list_invitations(team_name)
        return templates.TemplateResponse(
            "fragments/invitations.html",
            {
                "request": request,
                "team_name": team_name,
                "invitations": invitations,
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
    # Accept invitation (invitee side — no team yet)
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
        return templates.TemplateResponse(
            "fragments/acceptance_token.html",
            {
                "request": request,
                "acceptance_token": acceptance_token,
                "error": error,
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
            roles = m.get("station_roles", [])
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
