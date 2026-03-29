import httpx


class SmallSeaGotifyAdapter:
    """Adapter for publishing and polling notifications via a Gotify server.

    Gotify uses separate tokens for publishing (app token) and consuming (client
    token). Both are set at construction time. If only the app token is supplied,
    it is used for both roles — suitable when the Hub acts as a single trusted
    client with admin-level access.

    Polling is done via a simple HTTP GET rather than Gotify's WebSocket stream.
    The WebSocket path (for true push) is left for a future implementation.
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

    def poll(self, since="all", timeout=30):
        """GET /message with the client token. Returns a list of message dicts.

        since: "all" to return all stored messages, or a numeric string (Gotify
            message ID) to return only messages with id > that value.
        timeout: accepted for interface compatibility; this call does not
            long-poll — Gotify HTTP polling returns immediately.
        """
        try:
            resp = httpx.get(
                f"{self.base_url}/message",
                headers={"Authorization": f"Bearer {self.client_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        messages = data.get("messages", [])

        if since != "all":
            try:
                since_id = int(since)
                messages = [m for m in messages if m.get("id", 0) > since_id]
            except (ValueError, TypeError):
                pass

        return [
            {
                "id": str(m.get("id", "")),
                "message": m.get("message", ""),
                "title": m.get("title", ""),
                "time": m.get("date", ""),
            }
            for m in messages
        ]
