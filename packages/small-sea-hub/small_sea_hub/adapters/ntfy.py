import json

import httpx


class SmallSeaNtfyAdapter:
    """Adapter for publishing and polling notifications via an ntfy server."""

    def __init__(self, base_url, topic):
        self.base_url = base_url.rstrip("/")
        self.topic = topic

    def publish(self, message, title=None):
        """POST JSON to {base_url}/{topic}. Returns (ok, message_id, error_msg)."""
        url = f"{self.base_url}/{self.topic}"
        headers = {}
        if title:
            headers["Title"] = title
        try:
            resp = httpx.post(url, content=message, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return (True, data.get("id"), None)
        except Exception as e:
            return (False, None, str(e))

    def poll(self, since="all", timeout=30):
        """GET {base_url}/{topic}/json?poll=1&since={since}.
        Returns list of message dicts (only event="message", filters out open/keepalive).
        """
        url = f"{self.base_url}/{self.topic}/json"
        params = {"poll": "1", "since": since}
        try:
            resp = httpx.get(url, params=params, timeout=timeout + 5)
            resp.raise_for_status()
        except Exception:
            return []

        messages = []
        for line in resp.text.strip().splitlines():
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("event") == "message":
                messages.append(msg)
        return messages

    async def subscribe(self):
        """Async generator: yield message dicts from the ntfy SSE stream.

        Connects to {base_url}/{topic}/sse and yields each ntfy message event.
        The caller is responsible for reconnect logic.
        """
        url = f"{self.base_url}/{self.topic}/sse"
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("event") == "message":
                        yield msg
