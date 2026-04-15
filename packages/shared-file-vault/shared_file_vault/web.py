"""FastAPI + Jinja2 + htmx web UI for the Small Sea Shared File Vault."""

import pathlib

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from small_sea_client.client import SmallSeaClient

from shared_file_vault import sync, vault

_template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_template_dir)


def create_app(
    vault_root: str,
    participant_hex: str,
    hub_port: int = 11437,
    _http_client=None,
) -> FastAPI:
    """Create a configured FastAPI application."""
    app = FastAPI(title="Small Sea Vault")
    app.state.vault_root = vault_root
    app.state.participant_hex = participant_hex
    app.state.hub_port = hub_port
    app.state.http_client = _http_client
    app.state.pending_sessions = {}

    def _vr(request: Request) -> str:
        return request.app.state.vault_root

    def _ph(request: Request) -> str:
        return request.app.state.participant_hex

    def _hub_port(request: Request) -> int:
        return request.app.state.hub_port

    def _http_client(request: Request):
        return request.app.state.http_client

    def _client(request: Request) -> SmallSeaClient:
        return SmallSeaClient(port=_hub_port(request), _http_client=_http_client(request))

    def _pending(request: Request) -> dict[str, str]:
        return request.app.state.pending_sessions

    def _session_state(request: Request, team_name: str) -> str:
        try:
            sync.get_team_session(
                team_name,
                hub_port=_hub_port(request),
                _http_client=_http_client(request),
            )
            return "active"
        except sync.LoginRequiredError:
            return "pending" if team_name in _pending(request) else "none"

    def _session_fragment(request: Request, team_name: str, error: str | None = None):
        return templates.TemplateResponse(
            "fragments/team_session.html",
            {
                "request": request,
                "team_name": team_name,
                "team_session_status": _session_state(request, team_name),
                "session_error": error,
            },
        )

    def _niche_detail_response(
        request: Request,
        team_name: str,
        niche_name: str,
        *,
        sync_notice: str | None = None,
        sync_error: str | None = None,
        from_member_id: str = "",
    ):
        vr, ph = _vr(request), _ph(request)
        team_session_status = _session_state(request, team_name)
        if team_session_status == "active":
            try:
                peers = sync.list_team_peers(
                    team_name,
                    hub_port=_hub_port(request),
                    _http_client=_http_client(request),
                )
                for peer in peers:
                    peer["update_status"] = sync.peer_update_status(
                        vr,
                        ph,
                        team_name,
                        niche_name,
                        peer["member_id"],
                    )
            except sync.VaultSyncError:
                peers = []
        else:
            peers = []
        checkout = vault.get_checkout(vr, ph, team_name, niche_name)
        checkout_status = (
            vault.status(vr, ph, team_name, niche_name, checkout)
            if checkout is not None
            else []
        )
        commits = vault.log(vr, ph, team_name, niche_name)
        return templates.TemplateResponse(
            "fragments/niche_detail.html",
            {
                "request": request,
                "team_name": team_name,
                "niche_name": niche_name,
                "checkout": checkout,
                "checkout_status": checkout_status,
                "commits": commits,
                "sync_notice": sync_notice,
                "sync_error": sync_error,
                "from_member_id": from_member_id,
                "team_session_status": team_session_status,
                "peers": peers,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        vr, ph = _vr(request), _ph(request)
        teams = vault.list_teams(vr, ph)
        team_data = [
            {
                "name": t,
                "niches": _niches_with_info(vr, ph, t),
            }
            for t in teams
        ]
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "teams": team_data, "participant_short": ph[:8]},
        )

    @app.post("/teams/create")
    async def create_team(
        request: Request,
        team_name: str = Form(...),
        niche_name: str = Form(...),
    ):
        """Create the first niche in a new team, then redirect to /."""
        vr, ph = _vr(request), _ph(request)
        vault.create_niche(vr, ph, team_name, niche_name)
        return RedirectResponse("/", status_code=303)

    @app.post("/teams/{team_name}/niches", response_class=HTMLResponse)
    async def create_niche(
        request: Request, team_name: str, niche_name: str = Form(...)
    ):
        vr, ph = _vr(request), _ph(request)
        try:
            vault.create_niche(vr, ph, team_name, niche_name)
            error = None
        except ValueError as e:
            error = str(e)
        return templates.TemplateResponse(
            "fragments/team_section.html",
            {
                "request": request,
                "team": {"name": team_name, "niches": _niches_with_info(vr, ph, team_name)},
                "error": error,
            },
        )

    @app.get("/teams/{team_name}/niches/{niche_name}", response_class=HTMLResponse)
    async def niche_detail(request: Request, team_name: str, niche_name: str):
        return _niche_detail_response(request, team_name, niche_name)

    @app.post("/teams/{team_name}/session/request", response_class=HTMLResponse)
    async def team_session_request(request: Request, team_name: str):
        try:
            session, pending_id = _client(request).start_session(
                _ph(request), "SmallSeaCollectiveCore", team_name, "SharedFileVaultWeb"
            )
            if session is not None:
                sync.store_session_token(team_name, session.token)
                _pending(request).pop(team_name, None)
            else:
                _pending(request)[team_name] = pending_id
        except Exception as exc:
            return _session_fragment(request, team_name, error=str(exc))
        return _session_fragment(request, team_name)

    @app.post("/teams/{team_name}/session/confirm", response_class=HTMLResponse)
    async def team_session_confirm(
        request: Request, team_name: str, pin: str = Form(...)
    ):
        pending_id = _pending(request).get(team_name)
        if not pending_id:
            return _session_fragment(
                request, team_name, error="No pending session request for this team."
            )
        try:
            session = _client(request).confirm_session(pending_id, pin.strip())
            sync.store_session_token(team_name, session.token)
            _pending(request).pop(team_name, None)
        except Exception as exc:
            return _session_fragment(request, team_name, error=str(exc))
        return _session_fragment(request, team_name)

    @app.post(
        "/teams/{team_name}/session/resend-notification", response_class=HTMLResponse
    )
    async def team_session_resend(request: Request, team_name: str):
        pending_id = _pending(request).get(team_name)
        try:
            if pending_id:
                _client(request).resend_notification(pending_id)
        except Exception as exc:
            return _session_fragment(request, team_name, error=str(exc))
        return _session_fragment(request, team_name)

    @app.post("/teams/{team_name}/session/close", response_class=HTMLResponse)
    async def team_session_close(request: Request, team_name: str):
        _pending(request).pop(team_name, None)
        sync.clear_session_token(team_name)
        return _session_fragment(request, team_name)

    @app.post(
        "/teams/{team_name}/niches/{niche_name}/checkouts", response_class=HTMLResponse
    )
    async def add_checkout(
        request: Request,
        team_name: str,
        niche_name: str,
        dest_path: str = Form(...),
    ):
        vr, ph = _vr(request), _ph(request)
        try:
            vault.add_checkout(vr, ph, team_name, niche_name, dest_path)
            error = None
        except (ValueError, vault.DuplicateCheckoutError) as e:
            error = str(e)
        checkout = vault.get_checkout(vr, ph, team_name, niche_name)
        return templates.TemplateResponse(
            "fragments/checkouts.html",
            {
                "request": request,
                "team_name": team_name,
                "niche_name": niche_name,
                "checkout": checkout,
                "error": error,
            },
        )

    @app.post(
        "/teams/{team_name}/niches/{niche_name}/checkouts/remove",
        response_class=HTMLResponse,
    )
    async def remove_checkout(
        request: Request,
        team_name: str,
        niche_name: str,
        checkout_path: str = Form(...),
    ):
        vr, ph = _vr(request), _ph(request)
        vault.remove_checkout(vr, ph, team_name, niche_name, checkout_path)
        checkout = vault.get_checkout(vr, ph, team_name, niche_name)
        return templates.TemplateResponse(
            "fragments/checkouts.html",
            {
                "request": request,
                "team_name": team_name,
                "niche_name": niche_name,
                "checkout": checkout,
                "error": None,
            },
        )

    @app.post(
        "/teams/{team_name}/niches/{niche_name}/publish", response_class=HTMLResponse
    )
    async def publish(
        request: Request,
        team_name: str,
        niche_name: str,
        checkout_path: str = Form(...),
        message: str = Form(None),
    ):
        vr, ph = _vr(request), _ph(request)
        try:
            commit_hash = vault.publish(
                vr, ph, team_name, niche_name, checkout_path, message=message
            )
            notice = f"Published {commit_hash[:8]}"
            error = None
        except Exception as e:
            notice = None
            error = str(e)
        entries = vault.status(vr, ph, team_name, niche_name, checkout_path)
        commits = vault.log(vr, ph, team_name, niche_name)
        return templates.TemplateResponse(
            "fragments/status_panel.html",
            {
                "request": request,
                "team_name": team_name,
                "niche_name": niche_name,
                "checkout_path": checkout_path,
                "entries": entries,
                "commits": commits,
                "notice": notice,
                "error": error,
            },
        )

    @app.post(
        "/teams/{team_name}/niches/{niche_name}/push", response_class=HTMLResponse
    )
    async def push_sync(request: Request, team_name: str, niche_name: str):
        vr, ph, hub_port, http_client = (
            _vr(request),
            _ph(request),
            _hub_port(request),
            _http_client(request),
        )
        try:
            sync.push_via_hub(
                vr, ph, team_name, niche_name, hub_port=hub_port, _http_client=http_client
            )
            notice = "Pushed niche and registry through the Hub."
            error = None
        except sync.VaultSyncError as exc:
            notice = None
            error = str(exc)
        return _niche_detail_response(
            request,
            team_name,
            niche_name,
            sync_notice=notice,
            sync_error=error,
        )

    @app.post(
        "/teams/{team_name}/niches/{niche_name}/fetch", response_class=HTMLResponse
    )
    async def fetch_sync(
        request: Request,
        team_name: str,
        niche_name: str,
        from_member_id: str = Form(...),
    ):
        vr, ph, hub_port, http_client = (
            _vr(request),
            _ph(request),
            _hub_port(request),
            _http_client(request),
        )
        member_id = from_member_id.strip()
        try:
            sync.fetch_via_hub(
                vr,
                ph,
                team_name,
                niche_name,
                member_id,
                hub_port=hub_port,
                _http_client=http_client,
            )
            status = sync.peer_update_status(vr, ph, team_name, niche_name, member_id)
            if status.ready_to_merge:
                notice = f"Fetched changes from {member_id}. They are ready to merge."
            elif status.already_merged:
                notice = f"Fetched the latest changes from {member_id}. They are already merged."
            else:
                notice = f"Checked {member_id} for updates."
            error = None
        except sync.VaultSyncError as exc:
            notice = None
            error = str(exc)
        return _niche_detail_response(
            request,
            team_name,
            niche_name,
            sync_notice=notice,
            sync_error=error,
            from_member_id=member_id,
        )

    @app.post(
        "/teams/{team_name}/niches/{niche_name}/merge", response_class=HTMLResponse
    )
    async def merge_sync(
        request: Request,
        team_name: str,
        niche_name: str,
        from_member_id: str = Form(...),
    ):
        vr, ph, hub_port, http_client = (
            _vr(request),
            _ph(request),
            _hub_port(request),
            _http_client(request),
        )
        member_id = from_member_id.strip()
        try:
            sync.merge_via_hub(
                vr,
                ph,
                team_name,
                niche_name,
                member_id,
                hub_port=hub_port,
                _http_client=http_client,
            )
            notice = f"Merged parked changes from {member_id}."
            error = None
        except sync.DirtyCheckoutError as exc:
            notice = None
            path_list = ", ".join(exc.paths) if exc.paths else "unknown files"
            error = (
                f"Merge blocked: checkout has uncommitted changes ({path_list}). "
                "Publish or discard them before merging."
            )
        except sync.StaleCheckoutError as exc:
            notice = None
            error = (
                f"Merge blocked: registered checkout '{exc.checkout_path}' no longer exists. "
                "Remove the stale registration and re-attach at the correct path."
            )
        except sync.NoCheckoutError:
            notice = None
            error = "Merge blocked: no checkout is attached. Attach a checkout location first."
        except sync.PullConflictError as exc:
            notice = None
            if exc.paths:
                error = (
                    f"Merge left unresolved conflicts in the {exc.scope}: "
                    + ", ".join(exc.paths)
                )
            else:
                error = f"Merge left unresolved conflicts in the {exc.scope}."
        except sync.VaultSyncError as exc:
            notice = None
            error = str(exc)
        return _niche_detail_response(
            request,
            team_name,
            niche_name,
            sync_notice=notice,
            sync_error=error,
            from_member_id=member_id,
        )

    return app


def _niches_with_info(vault_root, participant_hex, team_name):
    """Return niches annotated with whether a checkout is attached."""
    niches = vault.list_niches(vault_root, participant_hex, team_name)
    result = []
    for n in niches:
        checkout = vault.get_checkout(vault_root, participant_hex, team_name, n["name"])
        result.append({**n, "has_checkout": checkout is not None})
    return result
