"""Async Hub session: the async twin of SmallSeaSession in client.py.

It has only the calls an async app uses so far. Errors are client.py's.

The session takes an httpx.AsyncClient whose base_url is the Hub. The caller
owns the client and closes it. Its timeout must outlast the longest watch the
caller asks for, because the Hub holds a watch open until something changes.
"""

import base64
from typing import Optional

import httpx

from small_sea_client.client import SmallSeaError, SmallSeaHubUnavailable, _check_response

#: The port a Hub listens on unless told otherwise.
DEFAULT_PORT = 11437


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Forwards requests to a transport that the injecting client owns.

    Closing the async client that uses it leaves the owner's transport open.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport):
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        pass


def async_transport_for(http_client, hub_url: str) -> httpx.AsyncBaseTransport | None:
    """The async transport that reaches the Hub an injected sync client reaches.

    Tests inject a sync client for the Hub; async calls must reach the same
    Hub. hub_url is the Hub's base URL, which picks the client's transport
    the way the client itself would, including a transport mounted for that
    URL. None means the async client opens its own network connections.
    """
    hub_app = getattr(http_client, "app", None)
    if hub_app is not None:
        # A Starlette TestClient: call the same app in-process.
        return httpx.ASGITransport(app=hub_app)
    if not isinstance(http_client, httpx.Client):
        return None
    transport = http_client._transport_for_url(httpx.URL(hub_url))
    if isinstance(transport, httpx.AsyncBaseTransport):
        # A transport that also serves async requests, such as MockTransport.
        return _BorrowedTransport(transport)
    return None


async def _hub_post(http: httpx.AsyncClient, path: str, payload: dict):
    try:
        resp = await http.post(path, json=payload)
    except httpx.ConnectError:
        raise SmallSeaHubUnavailable()
    _check_response(resp)
    return resp.json()


async def start_session(
    http: httpx.AsyncClient,
    participant: str,
    app: str,
    team: str,
    client_name: str,
    mode: str = "encrypted",
) -> "tuple[SmallSeaSession | None, str | None]":
    """Begin a session, in auto-approve or PIN mode.

    The async twin of SmallSeaClient.start_session. In auto-approve mode the
    Hub returns a token at once: (session, None). Otherwise it shows the
    person a PIN and returns (None, pending_id); pass both to confirm_session.
    """
    result = await _hub_post(http, "/sessions/request", {
        "participant": participant,
        "app": app,
        "team": team,
        "client": client_name,
        "mode": mode,
    })
    if "token" in result:
        return SmallSeaSession(http, result["token"]), None
    return None, result["pending_id"]


async def open_session(
    http: httpx.AsyncClient,
    participant: str,
    app: str,
    team: str,
    client_name: str,
    mode: str = "encrypted",
) -> "SmallSeaSession":
    """Open a session when the Hub is running in auto-approve mode.

    The async twin of SmallSeaClient.open_session. Raises SmallSeaError if
    the Hub is not in auto-approve mode.
    """
    session, _pending_id = await start_session(http, participant, app, team, client_name, mode)
    if session is None:
        raise SmallSeaError(
            "Hub is not in auto-approve mode; use start_session/confirm_session"
        )
    return session


async def confirm_session(http: httpx.AsyncClient, pending_id: str, pin: str) -> "SmallSeaSession":
    """Complete a PIN-mode session with the PIN the Hub showed the person.

    The async twin of SmallSeaClient.confirm_session. Raises SmallSeaError if
    the PIN is wrong or has expired.
    """
    token = await _hub_post(http, "/sessions/confirm", {"pending_id": pending_id, "pin": pin})
    return SmallSeaSession(http, token)


async def resend_notification(http: httpx.AsyncClient, pending_id: str) -> None:
    """Ask the Hub to show the PIN for a pending session again.

    The async twin of SmallSeaClient.resend_notification. Raises
    SmallSeaNotFound if the pending session has expired or does not exist.
    """
    await _hub_post(http, f"/sessions/{pending_id}/resend-notification", {})


async def create_bootstrap_session(
    http: httpx.AsyncClient,
    *,
    protocol: str,
    url: str,
    bucket: str,
    expires_at: str | None = None,
) -> str:
    """Ask the Hub for a token that reads one cloud location during bootstrap.

    The async twin of SmallSeaClient.create_bootstrap_session.
    """
    payload = {"protocol": protocol, "url": url, "bucket": bucket}
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return (await _hub_post(http, "/bootstrap/sessions", payload))["token"]


class SmallSeaSession:
    """An authenticated session with the Hub, scoped to one berth."""

    def __init__(self, http: httpx.AsyncClient, token: str):
        self._http = http
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    async def verify_history(self, git_dir, head: str) -> dict:
        """Ask the Hub to judge every commit reachable from head."""
        result = await self._post("/session/verify", {"git_dir": str(git_dir), "head": head})
        token = result["view_identifier"]
        result["view_identifier"] = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        return result

    async def ensure_cloud_ready(self) -> None:
        """Create and publish the cloud bucket for this session (S3 only).

        Must be called before the first push. Safe to call multiple times.
        """
        await self._post("/cloud/setup", {})

    async def ensure_berth_cloud_ready(self, berth_id_hex: str) -> None:
        """Manager sessions only: set up storage for another berth of this team."""
        await self._post(f"/manager/berths/{berth_id_hex}/cloud/setup", {})

    async def session_info(self) -> dict:
        """Return metadata for this session: participant_hex, team_name, app_name, berth_id, client."""
        return await self._get("/session/info")

    async def signing_public_key(self) -> str:
        """Return this session berth's workhorse public key."""
        return (await self._get("/session/signing_key"))["public_key"]

    async def authority_view(self) -> bytes:
        """Return the identifier of this session team's adopted authority view."""
        token = (await self._get("/session/authority_view"))["authority_view"]
        return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))

    async def session_peers(self) -> list[dict]:
        """Return peers visible to this session, with best-effort labels."""
        result = await self._get("/session/peers")
        return result.get("peers", [])

    async def list_sessions(self) -> list[dict]:
        """List this participant's confirmed Hub sessions, without tokens.

        Only a caller whose own session is on NoteToSelf may call this;
        other sessions raise SmallSeaError with code "session_admin_not_allowed".
        """
        return await self._get("/sessions/confirmed")

    async def delete_session(self, session_id: str) -> None:
        """Delete one of this participant's confirmed Hub sessions by id.

        Raises SmallSeaNotFound if the id names no session belonging to this
        participant. Only a caller whose own session is on NoteToSelf may
        call this.
        """
        await self._send("DELETE", f"/sessions/confirmed/{session_id}")

    async def list_granted_apps(self) -> list[dict]:
        """List app names that have ever received a confirmed session.

        Only a caller whose own session is on NoteToSelf may call this.
        """
        return await self._get("/apps/granted")

    async def app_sightings(self) -> list[dict]:
        """Return app-bootstrap sightings visible to this Manager/Core session."""
        return await self._get("/sightings")

    async def clear_app_sighting(
        self,
        *,
        app_name: str,
        team_name: str,
        client_name: str,
        last_seen_at: str,
    ) -> int:
        """Delete one Hub sighting whose tuple and last_seen_at still match.

        Pass the values from app_sightings() unchanged: the Hub compares
        last_seen_at as a string, so a reformatted timestamp misses the row.
        Returns the number of rows deleted, 0 or 1.
        """
        result = await self._post(
            "/sightings/clear",
            {
                "app_name": app_name,
                "team_name": team_name,
                "client_name": client_name,
                "last_seen_at": last_seen_at,
            },
        )
        return int(result["deleted_count"])

    async def prune_stale_app_sightings(self) -> int:
        """Delete this participant's Hub sightings older than the stale window.

        Returns the number of pruned rows.
        """
        return int((await self._post("/sightings/prune-stale", {}))["pruned_count"])

    async def watch_notifications(
        self,
        known: dict,
        timeout: int = 30,
        known_self_count: Optional[int] = None,
    ) -> dict:
        """Wait until a teammate's publication count exceeds the known one.

        known: {teammate_id_hex: last_known_count}. Returns at once if the Hub
        already has a higher count; otherwise waits up to timeout seconds.
        """
        payload = {"known": known, "timeout": timeout}
        if known_self_count is not None:
            payload["known_self_count"] = known_self_count
        return await self._post("/notifications/watch", payload)

    async def _post(self, path: str, json_data: dict):
        return await self._send("POST", path, json=json_data)

    async def _get(self, path: str):
        return await self._send("GET", path)

    async def _send(self, method: str, path: str, **kwargs):
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            resp = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.ConnectError:
            raise SmallSeaHubUnavailable()
        _check_response(resp)
        return resp.json()
