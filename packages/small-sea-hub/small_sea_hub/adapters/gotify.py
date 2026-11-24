import httpx


class SmallSeaGotifyAdapter:
    """Adapter for publishing notifications via a Gotify server.

    Gotify uses separate tokens for publishing (app token) and consuming (client
    token). Both are set at construction time. If only the app token is supplied,
    it is used for both roles — suitable when the Hub acts as a single trusted
    client with admin-level access.

    Nothing reads Gotify messages yet; a future subscriber would use the client
    token with Gotify's WebSocket stream.
    """

    def __init__(self, base_url, app_token, client_token=None):
        self.base_url = base_url.rstrip("/")
        self.app_token = app_token
        self.client_token = client_token or app_token

    def publish(self, message, title=None):
        """POST to /message with the app token. Returns (ok, message_id, error_msg)."""
        try:
            resp = httpx.post(
                f"{self.base_url}/message",
                json={"title": title or "", "message": message, "priority": 5},
                headers={"Authorization": f"Bearer {self.app_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return (True, str(data.get("id")), None)
        except Exception as e:
            return (False, None, str(e))
