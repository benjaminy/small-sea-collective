import base64
from typing import Optional

import httpx


class SmallSeaError(Exception):
    """Unexpected error response from the Hub."""


class SmallSeaHubUnavailable(Exception):
    """Could not connect to the Hub."""


class SmallSeaNotFound(SmallSeaError):
    """Requested file does not exist."""


class SmallSeaConflict(SmallSeaError):
    """Conditional upload failed: file was modified concurrently (CAS conflict)."""


def _check_response(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    if resp.status_code == 404:
        raise SmallSeaNotFound(detail)
    if resp.status_code == 409:
        raise SmallSeaConflict(detail)
    raise SmallSeaError(f"HTTP {resp.status_code}: {detail}")


class SmallSeaClient:
    """HTTP transport and session factory for the Small Sea Hub."""

    DEFAULT_PORT = 11437

    def __init__(self, port: int = DEFAULT_PORT, _http_client=None):
        self._base_url = f"http://127.0.0.1:{port}"
        self._http_client = _http_client

    def request_session(
        self,
        participant: str,
        app: str,
        team: str,
        client_name: str,
        mode: str = "encrypted",
    ) -> str:
        """Begin the two-step session flow.

        The Hub fires a system notification containing a 4-digit PIN. Returns
        the pending_id; pass it along with the PIN to confirm_session() once
        the user reads it.
        """
        result = self._post(
            "/sessions/request",
            {
                "participant": participant,
                "app": app,
                "team": team,
                "client": client_name,
                "mode": mode,
            },
        )
        return result["pending_id"]

    def start_session(
        self,
        participant: str,
        app: str,
        team: str,
        client_name: str,
        mode: str = "encrypted",
    ) -> "tuple[SmallSeaSession | None, str | None]":
        """Begin a session, handling both auto-approve and PIN modes in one call.

        Makes one request to /sessions/request and branches on the response:
        - Auto-approve: Hub returns token immediately → (session, None)
        - PIN required: Hub returns only pending_id → (None, pending_id);
          the Hub has already sent the PIN via OS notification; pass pending_id
          and the PIN to confirm_session() to complete the flow.
        """
        result = self._post(
            "/sessions/request",
            {
                "participant": participant,
                "app": app,
                "team": team,
                "client": client_name,
                "mode": mode,
            },
        )
        if "token" in result:
            return SmallSeaSession(self, result["token"]), None
        return None, result["pending_id"]

    def open_session(
        self,
        participant: str,
        app: str,
        team: str,
        client_name: str,
        mode: str = "encrypted",
    ) -> "SmallSeaSession":
        """Open a session when the Hub is running in auto-approve mode.

        The Hub must have SMALL_SEA_AUTO_APPROVE_SESSIONS=1 set. The PIN step
        is skipped entirely and a session token is returned immediately.
        Raises SmallSeaError if the Hub is not in auto-approve mode.
        """
        result = self._post(
            "/sessions/request",
            {
                "participant": participant,
                "app": app,
                "team": team,
                "client": client_name,
                "mode": mode,
            },
        )
        if "token" not in result:
            raise SmallSeaError(
                "Hub is not in auto-approve mode; use request_session/confirm_session"
            )
        return SmallSeaSession(self, result["token"])

    def resend_notification(self, pending_id: str) -> None:
        """Ask the Hub to re-fire the OS notification for a pending session.

        Use this when the user missed the original PIN notification. The PIN
        never appears in the HTTP response — it only travels via the OS
        notification. Raises SmallSeaNotFound if the pending session has
        expired or does not exist.
        """
        self._post(f"/sessions/{pending_id}/resend-notification", {})

    def confirm_session(self, pending_id: str, pin: str) -> "SmallSeaSession":
        """Complete the session flow with the PIN from the Hub notification.

        Returns a SmallSeaSession on success. Raises SmallSeaError if the PIN
        is wrong or expired.
        """
        token = self._post("/sessions/confirm", {"pending_id": pending_id, "pin": pin})
        return SmallSeaSession(self, token)

    def create_bootstrap_session(
        self,
        *,
        protocol: str,
        url: str,
        bucket: str,
        expires_at: str | None = None,
    ) -> str:
        payload = {
            "protocol": protocol,
            "url": url,
            "bucket": bucket,
        }
        if expires_at is not None:
            payload["expires_at"] = expires_at
        result = self._post("/bootstrap/sessions", payload)
        return result["token"]

    # ---- Internal HTTP helpers ----

    def _post(
        self,
        path: str,
        json_data: dict,
        *,
        token: Optional[str] = None,
    ):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        try:
            if self._http_client is not None:
                resp = self._http_client.post(path, json=json_data, headers=headers)
            else:
                resp = httpx.post(f"{self._base_url}{path}", json=json_data, headers=headers)
        except httpx.ConnectError:
            raise SmallSeaHubUnavailable()
        _check_response(resp)
        return resp.json()

    def _get(
        self,
        path: str,
        *,
        params: Optional[dict] = None,
        token: Optional[str] = None,
    ):
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        try:
            if self._http_client is not None:
                resp = self._http_client.get(path, params=params, headers=headers)
            else:
                resp = httpx.get(f"{self._base_url}{path}", params=params, headers=headers)
        except httpx.ConnectError:
            raise SmallSeaHubUnavailable()
        _check_response(resp)
        return resp.json()


class SmallSeaSession:
    """An authenticated session with the Hub, scoped to one berth."""

    def __init__(self, client: SmallSeaClient, token: str):
        self._client = client
        self._token = token
        self._last_notification_id: Optional[str] = None

    @property
    def token(self) -> str:
        """The session token. Can be persisted and passed to SmallSeaSession() to resume."""
        return self._token

    # ---- Session info ----

    def session_info(self) -> dict:
        """Return metadata for this session: participant_hex, team_name, app_name, berth_id, client."""
        return self._client._get("/session/info", token=self._token)

    def session_peers(self) -> list[dict]:
        """Return peers visible to this session, with best-effort labels."""
        result = self._client._get("/session/peers", token=self._token)
        return result.get("peers", [])

    def app_sightings(self) -> list[dict]:
        """Return app-bootstrap sightings visible to this Manager/Core session."""
        return self._client._get("/sightings", token=self._token)

    # ---- Cloud storage ----

    def ensure_cloud_ready(self) -> None:
        """Create and publish the cloud bucket for this session (S3 only).

        Must be called before the first push. Safe to call multiple times.
        """
        self._client._post("/cloud/setup", {}, token=self._token)

    def upload(self, path: str, data: bytes) -> str:
        """Unconditional upload. Creates or overwrites the file. Returns the etag."""
        result = self._client._post(
            "/cloud_file",
            {"path": path, "data": base64.b64encode(data).decode()},
            token=self._token,
        )
        return result["etag"]

    def upload_if_match(self, path: str, data: bytes, expected_etag: str) -> str:
        """Upload only if the file still matches expected_etag (compare-and-swap).

        Returns the new etag on success. Raises SmallSeaConflict if the file
        has been modified since expected_etag was read.
        """
        result = self._client._post(
            "/cloud_file",
            {
                "path": path,
                "data": base64.b64encode(data).decode(),
                "expected_etag": expected_etag,
            },
            token=self._token,
        )
        return result["etag"]

    def upload_create_only(self, path: str, data: bytes) -> str:
        """Upload only if no file exists at path yet. Returns the new etag.

        Raises SmallSeaConflict if a file already exists at path.

        Note: not yet implemented in the Hub.
        """
        raise NotImplementedError("upload_create_only is not yet implemented in the Hub")

    def download(self, path: str) -> tuple[bytes, str]:
        """Download a file. Returns (data, etag).

        Raises SmallSeaNotFound if no file exists at path.
        """
        result = self._client._get(
            "/cloud_file", params={"path": path}, token=self._token
        )
        return base64.b64decode(result["data"]), result["etag"]

    # ---- Sync notifications ----

    def watch_notifications(
        self,
        known: dict,
        timeout: int = 30,
        known_self_count: Optional[int] = None,
    ) -> dict:
        """Block until a sync count exceeds a known value.

        known: {member_id_hex: last_known_count} — the counts the caller has
            already processed. Returns immediately if the Hub already has higher
            counts; otherwise blocks up to timeout seconds.

        If known_self_count is provided, the Hub may also return
        {"self_updated_count": int} for NoteToSelf self-updates.
        """
        payload = {"known": known, "timeout": timeout}
        if known_self_count is not None:
            payload["known_self_count"] = known_self_count
        return self._client._post("/notifications/watch", payload, token=self._token)

    # ---- ntfy Notifications ----

    def send_notification(self, message: str, title: Optional[str] = None) -> str:
        """Send a notification to all berth members. Returns the message id."""
        body: dict = {"message": message}
        if title is not None:
            body["title"] = title
        result = self._client._post("/notifications", body, token=self._token)
        return result["id"]

    def poll_notifications(
        self, since: Optional[str] = None, timeout: int = 30
    ) -> list[dict]:
        """Poll for notifications since the given cursor.

        If since is provided it is used directly (pass "all" to fetch everything).
        Otherwise the internally tracked cursor from the last call is used; if
        there is no cursor yet, defaults to "all".

        The cursor is updated after each call that returns messages. Apps that
        need to resume after a restart should persist session.last_notification_id
        and pass it as since when recreating the session.
        """
        effective_since = since if since is not None else (self._last_notification_id or "all")
        result = self._client._get(
            "/notifications",
            params={"since": effective_since, "timeout": str(timeout)},
            token=self._token,
        )
        messages = result["messages"]
        if messages:
            self._last_notification_id = messages[-1]["id"]
        return messages

    @property
    def last_notification_id(self) -> Optional[str]:
        """The ID of the last notification seen, for persisting the poll cursor."""
        return self._last_notification_id
