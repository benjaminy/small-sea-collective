"""FastAPI + Jinja2 + htmx web UI for the Small Sea Shared File Vault."""

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared_file_vault import vault

template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=template_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # These must be set before starting, e.g. via environment or config
    app.state.vault_root = None
    app.state.participant_hex = None
    yield


app = FastAPI(lifespan=lifespan)


def _ctx(request: Request):
    return {
        "vault_root": request.app.state.vault_root,
        "participant_hex": request.app.state.participant_hex,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/teams/{team_name}/niches", response_class=HTMLResponse)
async def list_niches(request: Request, team_name: str):
    ctx = _ctx(request)
    niches = vault.list_niches(ctx["vault_root"], ctx["participant_hex"], team_name)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "team_name": team_name,
        "niches": niches,
    })


@app.post("/teams/{team_name}/niches", response_class=HTMLResponse)
async def create_niche(request: Request, team_name: str, niche_name: str = Form(...)):
    ctx = _ctx(request)
    vault.create_niche(ctx["vault_root"], ctx["participant_hex"], team_name, niche_name)
    niches = vault.list_niches(ctx["vault_root"], ctx["participant_hex"], team_name)
    return templates.TemplateResponse("fragments/niche_list.html", {
        "request": request,
        "team_name": team_name,
        "niches": niches,
    })


@app.get("/teams/{team_name}/niches/{niche_name}", response_class=HTMLResponse)
async def niche_detail(request: Request, team_name: str, niche_name: str):
    ctx = _ctx(request)
    try:
        entries = vault.status(ctx["vault_root"], ctx["participant_hex"], team_name, niche_name)
    except ValueError:
        entries = None
    try:
        commits = vault.log(ctx["vault_root"], ctx["participant_hex"], team_name, niche_name)
    except ValueError:
        commits = []
    return templates.TemplateResponse("niche_detail.html", {
        "request": request,
        "team_name": team_name,
        "niche_name": niche_name,
        "status_entries": entries,
        "commits": commits,
    })


@app.post("/teams/{team_name}/niches/{niche_name}/checkout", response_class=HTMLResponse)
async def checkout_niche(request: Request, team_name: str, niche_name: str, dest_path: str = Form(...)):
    ctx = _ctx(request)
    vault.checkout_niche(ctx["vault_root"], ctx["participant_hex"], team_name, niche_name, dest_path)
    entries = vault.status(ctx["vault_root"], ctx["participant_hex"], team_name, niche_name)
    return templates.TemplateResponse("fragments/status.html", {
        "request": request,
        "status_entries": entries,
        "message": f"Checked out to {dest_path}",
    })


@app.post("/teams/{team_name}/niches/{niche_name}/publish", response_class=HTMLResponse)
async def publish_niche(request: Request, team_name: str, niche_name: str, message: str = Form(None)):
    ctx = _ctx(request)
    commit_hash = vault.publish(ctx["vault_root"], ctx["participant_hex"], team_name, niche_name, message=message)
    entries = vault.status(ctx["vault_root"], ctx["participant_hex"], team_name, niche_name)
    return templates.TemplateResponse("fragments/status.html", {
        "request": request,
        "status_entries": entries,
        "message": f"Published: {commit_hash[:8]}",
    })
