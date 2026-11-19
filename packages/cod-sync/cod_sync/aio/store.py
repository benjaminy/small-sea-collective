"""Async bundle stores: the async twin of cod_sync.store.

Only the stores an async caller uses so far are here: the local folder, the
session's own Hub storage, and a teammate's storage read through the Hub.
Errors, object names and status classification are the sync module's.

A Hub store takes an httpx.AsyncClient whose base_url is the Hub. The caller
owns the client and closes it, so one connection pool can serve every store
and session object an app holds.
"""

import base64
from typing import Optional, Tuple

import httpx

from cod_sync import store as _sync
from cod_sync.store import (
    CREATE_ONLY,
    LATEST_LINK_PATH,
    DownloadTooLargeError,
    MalformedStoreResponseError,
    PublicationOutcomeUnknownError,
    StoreError,
    StoreTransportError,
    bundle_path,
    link_path,
)


class LocalFolderStore:
    """The sync LocalFolderStore behind async methods.

    Its reads and writes are small local file operations, so they run on the
    event loop rather than in a thread. A head replacement holds an flock, but
    only within one call, so two publishers on one loop cannot deadlock.
    """

    def __init__(self, path):
        self._store = _sync.LocalFolderStore(path)
        self.path = self._store.path

    async def get_latest_link(self) -> Tuple[bytes, Optional[str]]:
        return self._store.get_latest_link()

    async def get_link(self, link_uid: str) -> bytes:
        return self._store.get_link(link_uid)

    async def download_bundle(self, bundle_uid: str, local_path) -> None:
        self._store.download_bundle(bundle_uid, local_path)

    async def put_bundle(self, bundle_uid: str, local_path) -> None:
        self._store.put_bundle(bundle_uid, local_path)

    async def put_link(self, link_uid: str, data: bytes) -> None:
        self._store.put_link(link_uid, data)

    async def put_latest_link(
        self, data: bytes, expected_etag: Optional[str], link_uid: Optional[str] = None
    ) -> Optional[str]:
        return self._store.put_latest_link(data, expected_etag, link_uid=link_uid)


class _HubStore:
    """Shared HTTP handling for every async store that reaches the Hub."""

    def __init__(
        self,
        session_hex: str,
        client: httpx.AsyncClient,
        path_prefix: str = "",
        max_download_bytes: int = 256 * 1024 * 1024,
    ):
        self.session_hex = session_hex
        self._client = client
        self._auth = {"Authorization": f"Bearer {session_hex}"}
        self._path_prefix = path_prefix
        self._max_download_bytes = max_download_bytes

    def _download_endpoint(self, cloud_path: str) -> Tuple[str, dict]:
        raise NotImplementedError

    # The sync stores' status classification, unchanged.
    _detail = staticmethod(_sync._HubStore._detail)
    _classify = _sync._HubStore._classify

    async def _post(self, path: str, **kwargs):
        try:
            return await self._client.post(path, **kwargs)
        except Exception as exc:
            raise StoreTransportError(f"request failed: {exc}") from exc

    async def _download(self, cloud_path: str) -> Tuple[bytes, Optional[str]]:
        data = bytearray()
        etag = await self._read_download(cloud_path, data.extend)
        return bytes(data), etag

    async def _read_download(self, cloud_path: str, write_chunk) -> Optional[str]:
        endpoint, params = self._download_endpoint(cloud_path)
        try:
            async with self._client.stream(
                "GET", endpoint, params=params, headers=self._auth
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    raise self._classify(resp, cloud_path)
                content_type = resp.headers.get("content-type", "").split(";", 1)[0]
                if content_type != "application/octet-stream":
                    raise MalformedStoreResponseError(
                        f"{cloud_path}: expected application/octet-stream, got {content_type!r}"
                    )
                length = resp.headers.get("content-length")
                expected = int(length) if length is not None else None
                if expected is not None and expected > self._max_download_bytes:
                    raise DownloadTooLargeError(cloud_path, self._max_download_bytes)
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > self._max_download_bytes:
                        raise DownloadTooLargeError(cloud_path, self._max_download_bytes)
                    write_chunk(chunk)
                if expected is not None and total != expected:
                    raise StoreTransportError(
                        f"{cloud_path}: response ended at {total} of {expected} bytes"
                    )
                return resp.headers.get("etag")
        except StoreError:
            raise
        except Exception as exc:
            raise StoreTransportError(f"{cloud_path}: download failed: {exc}") from exc

    async def get_latest_link(self) -> Tuple[bytes, Optional[str]]:
        return await self._download(LATEST_LINK_PATH)

    async def get_link(self, link_uid: str) -> bytes:
        return (await self._download(link_path(link_uid)))[0]

    async def download_bundle(self, bundle_uid: str, local_path) -> None:
        with open(local_path, "wb") as handle:
            await self._read_download(bundle_path(bundle_uid), handle.write)


class SmallSeaStore(_HubStore):
    """The session's own cloud storage, reached through the Hub."""

    def _download_endpoint(self, cloud_path: str):
        return "/cloud_file", {"path": self._path_prefix + cloud_path}

    async def _upload(
        self,
        cloud_path: str,
        data: bytes,
        expected_etag: Optional[str],
        notify: bool = False,
    ) -> Optional[str]:
        payload = {
            "path": self._path_prefix + cloud_path,
            "data": base64.b64encode(data).decode(),
        }
        if expected_etag is not None:
            payload["expected_etag"] = expected_etag
        if notify:
            payload["notify"] = True
        resp = await self._post("/cloud_file", json=payload, headers=self._auth)
        if resp.status_code != 200:
            raise self._classify(resp, cloud_path)
        try:
            return resp.json().get("etag")
        except Exception as exc:
            raise MalformedStoreResponseError(
                f"{cloud_path}: unreadable Hub response: {exc}"
            ) from exc

    async def put_bundle(self, bundle_uid: str, local_path) -> None:
        with open(local_path, "rb") as handle:
            data = handle.read()
        await self._upload(bundle_path(bundle_uid), data, CREATE_ONLY)

    async def put_link(self, link_uid: str, data: bytes) -> None:
        await self._upload(link_path(link_uid), data, CREATE_ONLY)

    async def put_latest_link(
        self, data: bytes, expected_etag: Optional[str], link_uid: Optional[str] = None
    ) -> Optional[str]:
        # notify=True tells the Hub to bump signals.yaml once the head moves.
        try:
            return await self._upload(
                LATEST_LINK_PATH,
                data,
                CREATE_ONLY if expected_etag is None else expected_etag,
                notify=True,
            )
        except (StoreTransportError, MalformedStoreResponseError) as exc:
            raise PublicationOutcomeUnknownError(
                f"the {LATEST_LINK_PATH} write may have taken effect: {exc}",
                expected_etag=expected_etag,
                link_uid=link_uid,
            ) from exc


class PeerSmallSeaStore(_HubStore):
    """Read-only view of a teammate's chain, proxied by the Hub."""

    def __init__(
        self,
        session_hex: str,
        teammate_id_hex: str,
        client: httpx.AsyncClient,
        path_prefix: str = "",
    ):
        super().__init__(session_hex, client, path_prefix=path_prefix)
        self.teammate_id_hex = teammate_id_hex

    _PREREQUISITE_ERRORS = _sync.PeerSmallSeaStore._PREREQUISITE_ERRORS

    def _download_endpoint(self, cloud_path: str):
        return "/peer_cloud_file", {
            "teammate_id": self.teammate_id_hex,
            "path": self._path_prefix + cloud_path,
        }

    def _classify(self, resp, cloud_path: str) -> StoreError:
        if resp.status_code == 409:
            try:
                error_code = resp.json().get("error")
            except Exception:
                error_code = None
            failure = self._PREREQUISITE_ERRORS.get(error_code)
            if failure is not None:
                return failure(f"{cloud_path}: {self._detail(resp)}")
        return super()._classify(resp, cloud_path)
