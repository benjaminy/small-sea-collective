"""Sandbox dashboard — FastAPI app and CLI."""

import os
import pathlib
import subprocess
import sys
from contextlib import asynccontextmanager
from typing import Optional

import click
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sandbox.workspace import (
    SandboxWorkspace,
    create_temp_workspace,
    is_port_in_use,
    minio_available,
)

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def create_app(workspace: Optional[SandboxWorkspace] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.workspace = workspace
        app.state.processes = {}  # key → Popen
        yield
        # Terminate all child processes on shutdown
        for proc in app.state.processes.values():
            if proc.poll() is None:
                proc.terminate()

    app = FastAPI(lifespan=lifespan)

    def _render(template_name: str, request: Request, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(
            request, template_name, {"request": request, **ctx}
        )

    def _process_status(app, key: str) -> str:
        proc = app.state.processes.get(key)
        if proc is None:
            return "stopped"
        if proc.poll() is None:
            return "running"
        return "stopped"

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        ws = request.app.state.workspace
        if ws is None:
            return _render("index.html", request, workspace=None, minio_ok=minio_available())
        return _render(
            "index.html",
            request,
            workspace=ws,
            minio_ok=minio_available(),
            minio_status=_process_status(request.app, "minio"),
            participants=[
                {
                    "config": p,
                    "hub_status": _process_status(request.app, f"hub:{p.hex}"),
                    "manager_status": _process_status(request.app, f"manager:{p.hex}"),
                }
                for p in ws.participants
            ],
        )

    # ------------------------------------------------------------------
    # Workspace management
    # ------------------------------------------------------------------

    @app.post("/workspace")
    async def set_workspace(request: Request, workspace_path: str = Form(...)):
        path = pathlib.Path(workspace_path).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        request.app.state.workspace = SandboxWorkspace.load(path)
        return RedirectResponse("/", status_code=303)

    @app.post("/workspace/temp")
    async def make_temp_workspace(request: Request):
        path = create_temp_workspace()
        request.app.state.workspace = SandboxWorkspace.load(path)
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------
    # MinIO
    # ------------------------------------------------------------------

    @app.post("/minio/start", response_class=HTMLResponse)
    async def minio_start(request: Request):
        ws = request.app.state.workspace
        key = "minio"
        proc = request.app.state.processes.get(key)
        error = None

        if not minio_available():
            error = "minio not found in PATH"
        elif proc is None or proc.poll() is not None:
            for port in (ws.minio.api_port, ws.minio.console_port):
                if is_port_in_use(port):
                    error = f"Port {port} is already in use"
                    break
            if not error:
                data_dir = ws.workspace_dir / "minio-data"
                data_dir.mkdir(exist_ok=True)
                env = {
                    **os.environ,
                    "MINIO_ROOT_USER": ws.minio.root_user,
                    "MINIO_ROOT_PASSWORD": ws.minio.root_password,
                }
                request.app.state.processes[key] = subprocess.Popen(
                    [
                        "minio",
                        "server",
                        str(data_dir),
                        "--address",
                        f":{ws.minio.api_port}",
                        "--console-address",
                        f":{ws.minio.console_port}",
                    ],
                    env=env,
                )

        return _render(
            "fragments/minio_card.html",
            request,
            workspace=ws,
            minio_ok=minio_available(),
            minio_status=_process_status(request.app, key),
            error=error,
        )

    @app.post("/minio/stop", response_class=HTMLResponse)
    async def minio_stop(request: Request):
        ws = request.app.state.workspace
        key = "minio"
        proc = request.app.state.processes.get(key)
        if proc and proc.poll() is None:
            proc.terminate()
        return _render(
            "fragments/minio_card.html",
            request,
            workspace=ws,
            minio_ok=minio_available(),
            minio_status=_process_status(request.app, key),
            error=None,
        )

    # ------------------------------------------------------------------
    # Participants
    # ------------------------------------------------------------------

    @app.post("/participants", response_class=HTMLResponse)
    async def add_participant(request: Request, nickname: str = Form(...)):
        ws = request.app.state.workspace
        ws.add_participant(nickname.strip())
        return _render(
            "fragments/participants_section.html",
            request,
            workspace=ws,
            participants=[
                {
                    "config": p,
                    "hub_status": _process_status(request.app, f"hub:{p.hex}"),
                    "manager_status": _process_status(request.app, f"manager:{p.hex}"),
                }
                for p in ws.participants
            ],
        )

    @app.post("/participants/{participant_hex}/hub/start", response_class=HTMLResponse)
    async def hub_start(request: Request, participant_hex: str):
        ws = request.app.state.workspace
        p = next((x for x in ws.participants if x.hex == participant_hex), None)
        error = None

        if p:
            key = f"hub:{p.hex}"
            proc = request.app.state.processes.get(key)
            if proc is None or proc.poll() is not None:
                if is_port_in_use(p.hub_port):
                    error = f"Port {p.hub_port} in use"
                else:
                    env = {
                        **os.environ,
                        "SMALL_SEA_ROOT_DIR": str(ws.workspace_dir),
                        "SMALL_SEA_AUTO_APPROVE_SESSIONS": "1",
                    }
                    request.app.state.processes[key] = subprocess.Popen(
                        [
                            sys.executable, "-m", "uvicorn",
                            "small_sea_hub.server:app",
                            "--host", "127.0.0.1",
                            "--port", str(p.hub_port),
                        ],
                        env=env,
                    )

        return _render(
            "fragments/participant_row.html",
            request,
            p=p,
            hub_status=_process_status(request.app, f"hub:{p.hex}"),
            manager_status=_process_status(request.app, f"manager:{p.hex}"),
            error=error,
        )

    @app.post("/participants/{participant_hex}/hub/stop", response_class=HTMLResponse)
    async def hub_stop(request: Request, participant_hex: str):
        ws = request.app.state.workspace
        p = next((x for x in ws.participants if x.hex == participant_hex), None)
        key = f"hub:{p.hex}"
        proc = request.app.state.processes.get(key)
        if proc and proc.poll() is None:
            proc.terminate()
        return _render(
            "fragments/participant_row.html",
            request,
            p=p,
            hub_status=_process_status(request.app, f"hub:{p.hex}"),
            manager_status=_process_status(request.app, f"manager:{p.hex}"),
            error=None,
        )

    @app.post("/participants/{participant_hex}/manager/start", response_class=HTMLResponse)
    async def manager_start(request: Request, participant_hex: str):
        ws = request.app.state.workspace
        p = next((x for x in ws.participants if x.hex == participant_hex), None)
        error = None

        if p:
            key = f"manager:{p.hex}"
            proc = request.app.state.processes.get(key)
            if proc is None or proc.poll() is not None:
                if is_port_in_use(p.manager_port):
                    error = f"Port {p.manager_port} in use"
                else:
                    request.app.state.processes[key] = subprocess.Popen(
                        [
                            sys.executable, "-m", "small_sea_manager.cli",
                            "--root-dir", str(ws.workspace_dir),
                            "--participant-hex", p.hex,
                            "--hub-port", str(p.hub_port),
                            "serve",
                            "--no-open",
                            "--port", str(p.manager_port),
                        ],
                    )

        return _render(
            "fragments/participant_row.html",
            request,
            p=p,
            hub_status=_process_status(request.app, f"hub:{p.hex}"),
            manager_status=_process_status(request.app, f"manager:{p.hex}"),
            error=error,
        )

    @app.post("/participants/{participant_hex}/manager/stop", response_class=HTMLResponse)
    async def manager_stop(request: Request, participant_hex: str):
        ws = request.app.state.workspace
        p = next((x for x in ws.participants if x.hex == participant_hex), None)
        key = f"manager:{p.hex}"
        proc = request.app.state.processes.get(key)
        if proc and proc.poll() is None:
            proc.terminate()
        return _render(
            "fragments/participant_row.html",
            request,
            p=p,
            hub_status=_process_status(request.app, f"hub:{p.hex}"),
            manager_status=_process_status(request.app, f"manager:{p.hex}"),
            error=None,
        )

    return app


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------


@click.command()
@click.option("--workspace", default=None, help="Path to sandbox workspace directory")
@click.option("--port", default=7000, show_default=True, help="Sandbox dashboard port")
@click.option("--host", default="127.0.0.1", show_default=True)
def cli(workspace, port, host):
    """Start the Small Sea sandbox dashboard."""
    import threading
    import webbrowser

    ws = None
    if workspace:
        p = pathlib.Path(workspace).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        ws = SandboxWorkspace.load(p)

    app = create_app(ws)
    url = f"http://{host}:{port}"
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    click.echo(f"Sandbox dashboard → {url}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
