# Top Matter

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from small_sea_team_manager.manager import TeamManager

template_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=template_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The manager is configured via environment or defaults.
    # In production use, the nickname would come from a login flow;
    # for now it's set on the app state and can be overridden.
    app.state.manager = TeamManager()
    app.state.nickname = None
    yield

app = FastAPI(lifespan=lifespan)


# --- Full pages ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    manager = request.app.state.manager
    teams = manager.list_teams()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "teams": teams,
        "nickname": request.app.state.nickname,
    })


@app.get("/teams/{team_name}", response_class=HTMLResponse)
async def team_detail(request: Request, team_name: str):
    manager = request.app.state.manager
    team = manager.get_team(team_name)
    members = manager.list_members(team_name)
    invitations = manager.list_invitations(team_name)
    return templates.TemplateResponse("team_detail.html", {
        "request": request,
        "team": team,
        "members": members,
        "invitations": invitations,
        "nickname": request.app.state.nickname,
    })


# --- htmx fragments ---

@app.get("/fragments/teams", response_class=HTMLResponse)
async def teams_fragment(request: Request):
    manager = request.app.state.manager
    teams = manager.list_teams()
    return templates.TemplateResponse("fragments/team_list.html", {
        "request": request,
        "teams": teams,
    })


@app.post("/fragments/teams", response_class=HTMLResponse)
async def create_team_fragment(request: Request, team_name: str = Form(...)):
    manager = request.app.state.manager
    try:
        manager.create_team(team_name)
        message = None
    except NotImplementedError:
        message = "Team creation not yet implemented."
    teams = manager.list_teams()
    return templates.TemplateResponse("fragments/team_list.html", {
        "request": request,
        "teams": teams,
        "message": message,
    })


@app.get("/fragments/teams/{team_name}/members", response_class=HTMLResponse)
async def members_fragment(request: Request, team_name: str):
    manager = request.app.state.manager
    members = manager.list_members(team_name)
    return templates.TemplateResponse("fragments/member_list.html", {
        "request": request,
        "team_name": team_name,
        "members": members,
    })


@app.post("/fragments/teams/{team_name}/invitations", response_class=HTMLResponse)
async def create_invitation_fragment(
        request: Request,
        team_name: str,
        invitee: str = Form(...)):
    manager = request.app.state.manager
    try:
        manager.create_invitation(team_name, invitee)
        message = f"Invited '{invitee}'."
    except NotImplementedError:
        message = "Invitations not yet implemented."
    invitations = manager.list_invitations(team_name)
    return templates.TemplateResponse("fragments/invite_form.html", {
        "request": request,
        "team_name": team_name,
        "invitations": invitations,
        "message": message,
    })


@app.delete("/fragments/teams/{team_name}/members/{member}", response_class=HTMLResponse)
async def remove_member_fragment(request: Request, team_name: str, member: str):
    manager = request.app.state.manager
    try:
        manager.remove_member(team_name, member)
        message = None
    except NotImplementedError:
        message = "Member removal not yet implemented."
    members = manager.list_members(team_name)
    return templates.TemplateResponse("fragments/member_list.html", {
        "request": request,
        "team_name": team_name,
        "members": members,
        "message": message,
    })
