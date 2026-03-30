"""One-time Dropbox OAuth setup for a Small Sea participant.

Starts a local HTTP server to capture the OAuth redirect, so the user only
needs to visit a URL in their browser — no copy-pasting of auth codes.

Usage:
    uv run python devtools/setup_dropbox_auth.py
    uv run python devtools/setup_dropbox_auth.py --root-dir devtools/sandbox/
    uv run python devtools/setup_dropbox_auth.py --root-dir devtools/sandbox/ --participant <hex>

App credentials are required. Set env vars SMALL_SEA_DROPBOX_APP_KEY and
SMALL_SEA_DROPBOX_APP_SECRET, or pass --app-key / --app-secret flags.
See scripts/.env.example.

Before running, ensure your Dropbox app has
    http://localhost:9004
listed as an allowed redirect URI (Apps → OAuth 2 → Redirect URIs).
"""

import argparse
import http.server
import os
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

_DROPBOX_AUTH_URL = "https://www.dropbox.com/oauth2/authorize"
_DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
_REDIRECT_PORT = 9004
_REDIRECT_URI = f"http://localhost:{_REDIRECT_PORT}"


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def _build_auth_url(app_key: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": app_key,
        "response_type": "code",
        "token_access_type": "offline",
        "redirect_uri": _REDIRECT_URI,
    })
    return f"{_DROPBOX_AUTH_URL}?{params}"


def _exchange_code(app_key: str, app_secret: str, code: str) -> dict:
    resp = httpx.post(
        _DROPBOX_TOKEN_URL,
        data={
            "code": code,
            "grant_type": "authorization_code",
            "client_id": app_key,
            "client_secret": app_secret,
            "redirect_uri": _REDIRECT_URI,
        },
    )
    resp.raise_for_status()
    body = resp.json()
    expires_in = body.get("expires_in", 14400)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "token_expiry": expiry.isoformat(),
    }


def _capture_code() -> str:
    """Spin up a one-shot local HTTP server and return the auth code from the redirect."""
    code_holder = []
    event = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                code_holder.append(params["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Small Sea: Dropbox authorised.</h2>"
                    b"<p>You can close this tab.</p></body></html>"
                )
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"No code received.")
            event.set()

        def log_message(self, *args):
            pass  # suppress access logs

    server = http.server.HTTPServer(("localhost", _REDIRECT_PORT), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    event.wait(timeout=120)
    server.server_close()

    if not code_holder:
        raise RuntimeError("OAuth redirect not received within 120 seconds.")
    return code_holder[0]


# ---------------------------------------------------------------------------
# Participant discovery
# ---------------------------------------------------------------------------

def _list_participants(root_dir: Path) -> list[str]:
    participants_dir = root_dir / "Participants"
    if not participants_dir.exists():
        return []
    return sorted(d.name for d in participants_dir.iterdir() if d.is_dir())


def _pick_participant(root_dir: Path, hint: str | None) -> str:
    if hint:
        return hint
    participants = _list_participants(root_dir)
    if not participants:
        print(f"No participants found under {root_dir}/Participants/", file=sys.stderr)
        sys.exit(1)
    if len(participants) == 1:
        print(f"Participant: {participants[0][:16]}...")
        return participants[0]
    print("Multiple participants found:")
    for i, p in enumerate(participants):
        print(f"  [{i}] {p[:16]}...")
    idx = int(input("Choose index: ").strip())
    return participants[idx]


# ---------------------------------------------------------------------------
# Write credentials
# ---------------------------------------------------------------------------

def _write_credentials(
    root_dir: Path,
    participant_hex: str,
    app_key: str,
    app_secret: str,
    tokens: dict,
):
    db_path = root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    if not db_path.exists():
        print(
            f"\nError: NoteToSelf DB not found at:\n  {db_path}\n\n"
            "The participant directory exists but has not been initialised.\n"
            "Run the sandbox first (uv run small-sea-sandbox) to create a live\n"
            "workspace with initialised participants, then point this script at\n"
            "that workspace with --root-dir.\n\n"
            "Alternatively, use scripts/setup_dropbox_workspace.py to create a fresh\n"
            "Dropbox-only workspace without the sandbox.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Import provisioning from the workspace package tree.
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from small_sea_manager.provisioning import add_cloud_storage

    add_cloud_storage(
        root_dir,
        participant_hex,
        protocol="dropbox",
        url="https://www.dropboxapi.com",
        client_id=app_key,
        client_secret=app_secret,
        refresh_token=tokens["refresh_token"],
        access_token=tokens["access_token"],
        token_expiry=tokens["token_expiry"],
    )
    print(f"Credentials stored for participant {participant_hex[:16]}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Authorise a Small Sea participant with Dropbox."
    )
    parser.add_argument(
        "--root-dir",
        default="devtools/sandbox/",
        help="Workspace root directory (default: devtools/sandbox/)",
    )
    parser.add_argument(
        "--participant",
        help="Participant hex ID. Auto-selected if only one exists.",
    )
    parser.add_argument(
        "--app-key",
        default=os.environ.get("SMALL_SEA_DROPBOX_APP_KEY"),
        required=not os.environ.get("SMALL_SEA_DROPBOX_APP_KEY"),
        help="Dropbox app key (or set SMALL_SEA_DROPBOX_APP_KEY env var)",
    )
    parser.add_argument(
        "--app-secret",
        default=os.environ.get("SMALL_SEA_DROPBOX_APP_SECRET"),
        required=not os.environ.get("SMALL_SEA_DROPBOX_APP_SECRET"),
        help="Dropbox app secret (or set SMALL_SEA_DROPBOX_APP_SECRET env var)",
    )
    args = parser.parse_args()

    root_dir = Path(args.root_dir).expanduser().resolve()
    participant_hex = _pick_participant(root_dir, args.participant)

    print(f"\nOpening Dropbox authorization in your browser...")
    auth_url = _build_auth_url(args.app_key)
    webbrowser.open(auth_url)
    print(f"Waiting for redirect on {_REDIRECT_URI} ...")

    code = _capture_code()
    print("Code received. Exchanging for tokens...")

    tokens = _exchange_code(args.app_key, args.app_secret, code)
    _write_credentials(root_dir, participant_hex, args.app_key, args.app_secret, tokens)
    print("Done.")


if __name__ == "__main__":
    main()
