import json
from typing import Optional

import httpx

from .base import SmallSeaStorageAdapter

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"


class SmallSeaGDriveAdapter(SmallSeaStorageAdapter):
    """Google Drive adapter using the appDataFolder scope.

    Files are stored in the app-specific hidden folder, invisible to the user.
    Google Drive is ID-based, so we maintain a path→file_id mapping persisted
    as JSON in CloudStorage.path_metadata.
    """

    def __init__(self, access_token: str, path_metadata: dict[str, str] | None = None):
        super().__init__("appDataFolder")
        self.access_token = access_token
        self.path_ids: dict[str, str] = path_metadata or {}

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.access_token}"}
        if extra:
            h.update(extra)
        return h

    def get_path_metadata(self) -> dict[str, str]:
        """Return current path→ID map for persistence."""
        return dict(self.path_ids)

    def _find_file_id(self, path: str) -> str | None:
        """Look up a file ID by path, checking the cache then querying Drive."""
        if path in self.path_ids:
            return self.path_ids[path]

        resp = httpx.get(
            f"{DRIVE_API}/files",
            headers=self._headers(),
            params={
                "q": f"name='{path}' and 'appDataFolder' in parents and trashed=false",
                "spaces": "appDataFolder",
                "fields": "files(id,name)",
            },
        )
        resp.raise_for_status()
        files = resp.json().get("files", [])
        if files:
            file_id = files[0]["id"]
            self.path_ids[path] = file_id
            return file_id
        return None

    def download(self, path: str):
        file_id = self._find_file_id(path)
        if file_id is None:
            return False, None, "File not found"

        resp = httpx.get(
            f"{DRIVE_API}/files/{file_id}",
            headers=self._headers(),
            params={"alt": "media"},
        )
        if resp.status_code == 404:
            self.path_ids.pop(path, None)
            return False, None, "File not found"
        resp.raise_for_status()

        etag = resp.headers.get("ETag", "").strip('"')
        return True, resp.content, etag

    def _upload(
            self,
            path: str,
            data: bytes,
            expected_etag: Optional[str],
            content_type: str = "application/octet-stream"):
        file_id = self._find_file_id(path)

        if expected_etag == "*":
            # upload_fresh — must not already exist
            if file_id is not None:
                return False, None, "File already exists"
            return self._create_file(path, data, content_type)

        if file_id is None:
            # File doesn't exist yet — create it
            return self._create_file(path, data, content_type)

        # Update existing file
        headers = self._headers({"Content-Type": content_type})
        if expected_etag is not None:
            headers["If-Match"] = expected_etag

        resp = httpx.patch(
            f"{DRIVE_UPLOAD}/files/{file_id}",
            headers=headers,
            params={"uploadType": "media"},
            content=data,
        )

        if resp.status_code == 412:
            return False, None, "ETag mismatch - object was modified"

        resp.raise_for_status()
        body = resp.json()
        new_etag = resp.headers.get("ETag", "").strip('"')
        self.path_ids[path] = body["id"]
        return True, new_etag, "Object updated successfully"

    def _create_file(self, path: str, data: bytes, content_type: str):
        metadata = json.dumps({"name": path, "parents": ["appDataFolder"]})

        # Multipart upload: metadata + file content
        boundary = "small_sea_boundary"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{metadata}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()

        resp = httpx.post(
            f"{DRIVE_UPLOAD}/files",
            headers=self._headers({
                "Content-Type": f"multipart/related; boundary={boundary}",
            }),
            params={"uploadType": "multipart"},
            content=body,
        )
        resp.raise_for_status()
        result = resp.json()
        new_etag = resp.headers.get("ETag", "").strip('"')
        self.path_ids[path] = result["id"]
        return True, new_etag, "Object updated successfully"
