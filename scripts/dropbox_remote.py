"""Minimal direct-to-Dropbox Cod Sync store for bootstrapping test workspaces.

Used by setup_dropbox_workspace.py to move bundles and links straight to
Dropbox without a Hub. Not for production use — production traffic goes
through the Hub.
"""

import json

import httpx

from cod_sync.store import (
    CREATE_ONLY,
    LATEST_LINK_PATH,
    CasConflictError,
    ObjectNotFoundError,
    StoreProviderError,
    bundle_path,
    link_path,
)

DROPBOX_CONTENT = "https://content.dropboxapi.com/2"


class DropboxCodSyncStore:
    """Direct Dropbox store for Cod Sync publish and fetch.

    folder_prefix: e.g. "ss-{teammate_id_hex[:16]}" — every path is stored
        under this folder inside the app's Dropbox folder.
    """

    def __init__(self, access_token: str, folder_prefix: str = ""):
        self.access_token = access_token
        self.folder_prefix = folder_prefix.strip("/")

    def _make_path(self, path: str) -> str:
        if self.folder_prefix:
            return f"/{self.folder_prefix}/{path}"
        return f"/{path}"

    def _headers(self, extra=None):
        h = {"Authorization": f"Bearer {self.access_token}"}
        if extra:
            h.update(extra)
        return h

    def _upload(self, path: str, data: bytes, expected_etag) -> str:
        if expected_etag == CREATE_ONLY:
            mode = {".tag": "add"}
        elif expected_etag is None:
            mode = {".tag": "overwrite"}
        else:
            mode = {".tag": "update", "update": expected_etag}
        api_arg = json.dumps(
            {
                "path": self._make_path(path),
                "mode": mode,
                "autorename": False,
                "mute": True,
            }
        )
        resp = httpx.post(
            f"{DROPBOX_CONTENT}/files/upload",
            headers=self._headers(
                {
                    "Dropbox-API-Arg": api_arg,
                    "Content-Type": "application/octet-stream",
                }
            ),
            content=data,
            timeout=60,
        )
        # Dropbox reports a rejected write conflict as 409, whether the target
        # already existed or the rev no longer matches.
        if resp.status_code == 409:
            raise CasConflictError(f"{path}: {resp.text}")
        if resp.status_code != 200:
            raise StoreProviderError(f"writing {path} failed: HTTP {resp.status_code}")
        return resp.json().get("rev", "")

    def _download(self, path: str):
        api_arg = json.dumps({"path": self._make_path(path)})
        resp = httpx.post(
            f"{DROPBOX_CONTENT}/files/download",
            headers=self._headers({"Dropbox-API-Arg": api_arg}),
            timeout=60,
        )
        if resp.status_code == 409:
            raise ObjectNotFoundError(path)
        if resp.status_code != 200:
            raise StoreProviderError(f"reading {path} failed: HTTP {resp.status_code}")
        result = json.loads(resp.headers.get("Dropbox-API-Result", "{}"))
        return resp.content, result.get("rev", "")

    def get_latest_link(self):
        return self._download(LATEST_LINK_PATH)

    def get_link(self, link_uid: str) -> bytes:
        return self._download(link_path(link_uid))[0]

    def download_bundle(self, bundle_uid: str, local_path) -> None:
        data, _rev = self._download(bundle_path(bundle_uid))
        with open(local_path, "wb") as handle:
            handle.write(data)

    def put_bundle(self, bundle_uid: str, local_path) -> None:
        with open(local_path, "rb") as handle:
            self._upload(bundle_path(bundle_uid), handle.read(), CREATE_ONLY)

    def put_link(self, link_uid: str, data: bytes) -> None:
        self._upload(link_path(link_uid), data, CREATE_ONLY)

    def put_latest_link(self, data: bytes, expected_etag, link_uid=None):
        return self._upload(
            LATEST_LINK_PATH, data, CREATE_ONLY if expected_etag is None else expected_etag
        )
