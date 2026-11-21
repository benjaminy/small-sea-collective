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
    payload = {
        "participant": participant,
        "app": app,
        "team": team,
        "client": client_name,
        "mode": mode,
    }
    try:
        resp = await http.post("/sessions/request", json=payload)
    except httpx.ConnectError:
        raise SmallSeaHubUnavailable()
    _check_response(resp)
    result = resp.json()
    if "token" not in result:
        raise SmallSeaError(
            "Hub is not in auto-approve mode; use request_session/confirm_session"
        )
    return SmallSeaSession(http, result["token"])


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
