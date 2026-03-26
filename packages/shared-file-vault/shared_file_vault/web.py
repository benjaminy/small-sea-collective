"""FastAPI + Jinja2 + htmx web UI for the Small Sea Shared File Vault."""

import pathlib

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared_file_vault import vault

_template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=_template_dir)


def create_app(vault_root: str, participant_hex: str) -> FastAPI:
    """Create a configured FastAPI application."""
    app = FastAPI(title="Small Sea Vault")
    app.state.vault_root = vault_root
    app.state.participant_hex = participant_hex

    def _vr(request: Request) -> str:
        return request.app.state.vault_root

    def _ph(request: Request) -> str:
        return request.app.state.participant_hex

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
        vr, ph = _vr(request), _ph(request)
        checkouts = vault.list_checkouts(vr, ph, team_name, niche_name)
        status_by_checkout = [
            {"path": co, "entries": vault.status(vr, ph, team_name, niche_name, co)}
            for co in checkouts
        ]
        commits = vault.log(vr, ph, team_name, niche_name)
        return templates.TemplateResponse(
            "fragments/niche_detail.html",
            {
                "request": request,
                "team_name": team_name,
                "niche_name": niche_name,
                "checkouts": checkouts,
                "status_by_checkout": status_by_checkout,
                "commits": commits,
            },
        )

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
        except ValueError as e:
            error = str(e)
        checkouts = vault.list_checkouts(vr, ph, team_name, niche_name)
        return templates.TemplateResponse(
            "fragments/checkouts.html",
            {
                "request": request,
                "team_name": team_name,
                "niche_name": niche_name,
                "checkouts": checkouts,
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
        checkouts = vault.list_checkouts(vr, ph, team_name, niche_name)
        return templates.TemplateResponse(
            "fragments/checkouts.html",
            {
                "request": request,
                "team_name": team_name,
                "niche_name": niche_name,
                "checkouts": checkouts,
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

    return app


def _niches_with_info(vault_root, participant_hex, team_name):
    """Return niches annotated with local checkout count."""
    niches = vault.list_niches(vault_root, participant_hex, team_name)
    result = []
    for n in niches:
        checkouts = vault.list_checkouts(
            vault_root, participant_hex, team_name, n["name"]
        )
        result.append({**n, "checkout_count": len(checkouts)})
    return result
