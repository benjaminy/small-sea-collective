"""Minimal Dropbox CodSync remote for bootstrapping test workspaces.

Used by setup_dropbox_workspace.py to push/pull git objects directly to Dropbox
without going through the Hub. Not for production use.
"""

import io
import json

import httpx
import yaml
from cod_sync.protocol import CodSyncRemote

DROPBOX_CONTENT = "https://content.dropboxapi.com/2"


class DropboxCodSyncRemote(CodSyncRemote):
    """Direct Dropbox remote — wraps the Dropbox API for CodSync push/pull.

    folder_prefix: e.g. "ss-{member_id_hex[:16]}" — all paths are stored under
        this folder inside the app's Dropbox folder.
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

    def _upload(self, path: str, data: bytes, expected_etag=None) -> str:
        if expected_etag is None:
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
        resp.raise_for_status()
        return resp.json().get("rev", "")

    def _download(self, path: str):
        api_arg = json.dumps({"path": self._make_path(path)})
        resp = httpx.post(
            f"{DROPBOX_CONTENT}/files/download",
            headers=self._headers({"Dropbox-API-Arg": api_arg}),
            timeout=60,
        )
        if resp.status_code == 409:
            return None, None
        resp.raise_for_status()
        result = json.loads(resp.headers.get("Dropbox-API-Result", "{}"))
        rev = result.get("rev", "")
        return resp.content, rev

    def upload_latest_link(
        self, link_uid, blob, bundle_uid, local_bundle_path, expected_etag=None
    ):
        with open(local_bundle_path, "rb") as f:
            bundle_bytes = f.read()
        self._upload(f"B-{bundle_uid}.bundle", bundle_bytes)
        link_yaml = yaml.dump(blob, default_flow_style=False).encode("utf-8")
        self._upload(f"L-{link_uid}.yaml", link_yaml)
        self._upload("latest-link.yaml", link_yaml, expected_etag=expected_etag)

    def get_latest_link(self):
        data, etag = self._download("latest-link.yaml")
        if data is None:
            return None
        link = self.read_link_blob(io.BytesIO(data))
        return (link, etag)

    def get_link(self, uid):
        data, _ = self._download(f"L-{uid}.yaml")
        if data is None:
            return None
        return self.read_link_blob(io.BytesIO(data))

    def download_bundle(self, bundle_uid, local_bundle_path):
        data, _ = self._download(f"B-{bundle_uid}.bundle")
        if data is None:
            raise RuntimeError(f"Failed to download bundle B-{bundle_uid}.bundle")
        with open(local_bundle_path, "wb") as f:
            f.write(data)
