import json
from typing import Optional

import httpx

from .base import SmallSeaStorageAdapter

DROPBOX_CONTENT = "https://content.dropboxapi.com/2"


class SmallSeaDropboxAdapter(SmallSeaStorageAdapter):
    """Dropbox adapter using app-folder access.

    Path-based API — simpler than Google Drive, no ID mapping needed.
    Conditional writes use the Dropbox `rev` field as the ETag equivalent.
    """

    def __init__(self, access_token: str):
        super().__init__("dropbox")
        self.access_token = access_token

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.access_token}"}
        if extra:
            h.update(extra)
        return h

    def download(self, path: str):
        api_arg = json.dumps({"path": f"/{path}"})
        resp = httpx.post(
            f"{DROPBOX_CONTENT}/files/download",
            headers=self._headers({"Dropbox-API-Arg": api_arg}),
        )

        if resp.status_code == 409:
            return False, None, "File not found"

        resp.raise_for_status()

        # Dropbox returns file metadata in the Dropbox-API-Result header
        result_header = resp.headers.get("Dropbox-API-Result", "{}")
        result = json.loads(result_header)
        rev = result.get("rev", "")
        return True, resp.content, rev

    def _upload(
            self,
            path: str,
            data: bytes,
            expected_etag: Optional[str],
            content_type: str = "application/octet-stream"):

        if expected_etag is None:
            mode = {".tag": "overwrite"}
        elif expected_etag == "*":
            mode = {".tag": "add"}
        else:
            mode = {".tag": "update", "update": expected_etag}

        api_arg = json.dumps({
            "path": f"/{path}",
            "mode": mode,
            "autorename": False,
            "mute": True,
        })

        resp = httpx.post(
            f"{DROPBOX_CONTENT}/files/upload",
            headers=self._headers({
                "Dropbox-API-Arg": api_arg,
                "Content-Type": "application/octet-stream",
            }),
            content=data,
        )

        if resp.status_code == 409:
            body = resp.json()
            error_tag = body.get("error", {}).get(".tag", "")
            if error_tag == "path" and expected_etag == "*":
                return False, None, "File already exists"
            if error_tag == "path":
                reason = body.get("error", {}).get("reason", {}).get(".tag", "")
                if reason == "conflict":
                    return False, None, "ETag mismatch - object was modified"
            return False, None, f"Upload failed: {body.get('error_summary', 'unknown')}"

        resp.raise_for_status()
        result = resp.json()
        rev = result.get("rev", "")
        return True, rev, "Object updated successfully"
