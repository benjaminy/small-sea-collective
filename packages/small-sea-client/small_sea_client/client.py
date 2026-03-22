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

    def __init__(self, port: int = DEFAULT_PORT):
        self._base_url = f"http://127.0.0.1:{port}"

    def request_session(
        self, participant: str, app: str, team: str, client_name: str
    ) -> str:
        """Begin the two-step session flow.

        The Hub fires a system notification containing a 4-digit PIN. Returns
        the pending_id; pass it along with the PIN to confirm_session() once
        the user reads it.
        """
        result = self._post(
            "/sessions/request",
            {"participant": participant, "app": app, "team": team, "client": client_name},
        )
        return result["pending_id"]

    def confirm_session(self, pending_id: str, pin: str) -> "SmallSeaSession":
        """Complete the session flow with the PIN from the Hub notification.

        Returns a SmallSeaSession on success. Raises SmallSeaError if the PIN
        is wrong or expired.
        """
        token = self._post("/sessions/confirm", {"pending_id": pending_id, "pin": pin})
        return SmallSeaSession(self, token)

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
            resp = httpx.get(f"{self._base_url}{path}", params=params, headers=headers)
        except httpx.ConnectError:
            raise SmallSeaHubUnavailable()
        _check_response(resp)
        return resp.json()


class SmallSeaSession:
    """An authenticated session with the Hub, scoped to one station."""

    def __init__(self, client: SmallSeaClient, token: str):
        self._client = client
        self._token = token
        self._last_notification_id: Optional[str] = None

    @property
    def token(self) -> str:
        """The session token. Can be persisted and passed to SmallSeaSession() to resume."""
        return self._token

    # ---- Cloud storage ----

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

    # ---- Notifications ----

    def send_notification(self, message: str, title: Optional[str] = None) -> str:
        """Send a notification to all station members. Returns the message id."""
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
