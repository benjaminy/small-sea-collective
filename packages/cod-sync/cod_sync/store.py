"""Bundle stores: dumb, truthful byte transports.

A store reads and writes opaque link bytes and bundle files. It does not parse
YAML, know Git semantics, or interpret a link's contents. Its whole
responsibility is to move bytes and report accurately what happened, because
every safety property above it rests on one distinction: an object that is
exactly missing versus a read that failed for any other reason.

Object naming inside a store:

    latest-link.yaml    the current head of the chain, the serialization point
    L-{link_uid}.yaml   an archived link, written once and never replaced
    B-{bundle_uid}.bundle   a bundle, written once and never replaced

Production stores reach the network only through the Hub. LocalFolderStore
performs local filesystem I/O; the direct-provider stores in cod_sync.testing
are test infrastructure, not a production exception to the gateway rule.
"""

import base64
import fcntl
import hashlib
import logging
import os
import pathlib
import shutil
import tempfile
from typing import Optional, Protocol, Tuple

import requests

logger = logging.getLogger("cod_sync")

LATEST_LINK_PATH = "latest-link.yaml"

#: Passed as expected_etag to request a create-only write.
CREATE_ONLY = "*"


def link_path(link_uid: str) -> str:
    return f"L-{link_uid}.yaml"


def bundle_path(bundle_uid: str) -> str:
    return f"B-{bundle_uid}.bundle"


# ---------------------------------------------------------------------- #
# Typed transport results
# ---------------------------------------------------------------------- #


class StoreError(Exception):
    """Base class for every store failure."""


class ObjectNotFoundError(StoreError):
    """The store confirmed this exact object does not exist.

    The only failure a caller may read as "empty store" or "missing
    predecessor". Every other failure leaves the store's contents unknown.
    """

    def __init__(self, path: str):
        super().__init__(f"no such object: {path}")
        self.path = path


class StoreAuthenticationError(StoreError):
    """The store rejected the caller's credentials."""


class StoreAuthorizationError(StoreError):
    """The caller authenticated but is not permitted to touch this object."""


class StoreProviderError(StoreError):
    """The Hub or the cloud provider behind it failed.

    The object's existence is unknown.
    """


class StoreTransportError(StoreError):
    """The request did not complete: connection, timeout, or similar."""


class MalformedStoreResponseError(StoreError):
    """The transport answered, but not with something this store can read."""


class CasConflictError(StoreError):
    """A create-only or compare-and-swap write lost to a concurrent writer."""


class PublicationOutcomeUnknownError(StoreError):
    """The final head write may or may not have taken effect.

    Retrying blindly could overwrite a competing publication that actually
    won, so the caller rereads instead.
    """

    def __init__(self, message: str, expected_etag: Optional[str], link_uid: Optional[str] = None):
        super().__init__(message)
        self.expected_etag = expected_etag
        self.link_uid = link_uid


# ---------------------------------------------------------------------- #
# Interfaces
# ---------------------------------------------------------------------- #


class ReadableBundleStore(Protocol):
    """Everything fetch needs, and nothing publication needs."""

    def get_latest_link(self) -> Tuple[bytes, Optional[str]]:
        """Return the current head link's bytes and its etag.

        Raises ObjectNotFoundError when the store holds no chain yet.
        """

    def get_link(self, link_uid: str) -> bytes:
        """Return an archived link's bytes."""

    def download_bundle(self, bundle_uid: str, local_path) -> None:
        """Write a bundle's bytes to local_path."""


class WritableBundleStore(ReadableBundleStore, Protocol):
    """Adds the three writes publication performs, in that order."""

    def put_bundle(self, bundle_uid: str, local_path) -> None:
        """Create a bundle object. A colliding uid raises CasConflictError."""

    def put_link(self, link_uid: str, data: bytes) -> None:
        """Create an archived link. A colliding uid raises CasConflictError."""

    def put_latest_link(
        self, data: bytes, expected_etag: Optional[str], link_uid: Optional[str] = None
    ) -> Optional[str]:
        """Move the chain head, the moment a publication becomes visible.

        expected_etag None creates the first head and fails with
        CasConflictError if any head already exists; otherwise the write is a
        compare-and-swap against that etag. Returns the new etag when the
        transport reports one.
        """


# ---------------------------------------------------------------------- #
# Local folder
# ---------------------------------------------------------------------- #


class LocalFolderStore:
    """A local directory pretending to be cloud storage.

    Holds the same create-only, write-once, and atomic-CAS contract as the
    Hub-backed stores rather than approximating it, so tests that use it are
    testing the real rules.
    """

    def __init__(self, path):
        self.path = pathlib.Path(path)
        if not self.path.is_dir():
            raise StoreError(f"LocalFolderStore: not a directory '{path}'")

    def _full(self, name: str) -> pathlib.Path:
        return self.path / name

    @staticmethod
    def _etag_bytes(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _read(self, name: str) -> Tuple[bytes, str]:
        try:
            data = self._full(name).read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(name) from exc
        except OSError as exc:
            raise StoreProviderError(f"reading {name} failed: {exc}") from exc
        return data, self._etag_bytes(data)

    def _create_only(self, name: str, data: bytes) -> str:
        target = self._full(name)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as exc:
            raise CasConflictError(f"{name} already exists") from exc
        except OSError as exc:
            raise StoreProviderError(f"creating {name} failed: {exc}") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise StoreProviderError(f"writing {name} failed: {exc}") from exc
        return self._etag_bytes(data)

    def get_latest_link(self) -> Tuple[bytes, Optional[str]]:
        return self._read(LATEST_LINK_PATH)

    def get_link(self, link_uid: str) -> bytes:
        return self._read(link_path(link_uid))[0]

    def download_bundle(self, bundle_uid: str, local_path) -> None:
        source = self._full(bundle_path(bundle_uid))
        try:
            shutil.copyfile(source, local_path)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(bundle_path(bundle_uid)) from exc
        except OSError as exc:
            raise StoreProviderError(
                f"reading {bundle_path(bundle_uid)} failed: {exc}"
            ) from exc

    def put_bundle(self, bundle_uid: str, local_path) -> None:
        with open(local_path, "rb") as handle:
            self._create_only(bundle_path(bundle_uid), handle.read())

    def put_link(self, link_uid: str, data: bytes) -> None:
        self._create_only(link_path(link_uid), data)

    def put_latest_link(
        self, data: bytes, expected_etag: Optional[str], link_uid: Optional[str] = None
    ) -> Optional[str]:
        if expected_etag is None:
            return self._create_only(LATEST_LINK_PATH, data)

        # Compare and replace under one lock so a concurrent publisher cannot
        # slip between reading the current etag and installing the new head.
        target = self._full(LATEST_LINK_PATH)
        lock_path = self._full(".latest-link.lock")
        with open(lock_path, "a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    current = target.read_bytes()
                except FileNotFoundError as exc:
                    raise CasConflictError(
                        f"{LATEST_LINK_PATH} does not exist but an etag was expected"
                    ) from exc
                current_etag = self._etag_bytes(current)
                if current_etag != expected_etag:
                    raise CasConflictError(
                        f"{LATEST_LINK_PATH} is at etag {current_etag}, expected {expected_etag}"
                    )
                handle, temp_name = tempfile.mkstemp(dir=self.path, prefix=".latest-link-")
                try:
                    with os.fdopen(handle, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp_name, target)
                except OSError as exc:
                    os.unlink(temp_name)
                    raise StoreProviderError(
                        f"replacing {LATEST_LINK_PATH} failed: {exc}"
                    ) from exc
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return self._etag_bytes(data)


# ---------------------------------------------------------------------- #
# Hub-backed stores
# ---------------------------------------------------------------------- #


class _HubStore:
    """Shared HTTP handling for every store that reaches the Hub.

    Subclasses supply the endpoint and its parameters; this class owns the
    single place where a status code becomes a typed result.
    """

    def __init__(self, session_hex: str, base_url: str, client=None, path_prefix: str = ""):
        self.session_hex = session_hex
        self._auth = {"Authorization": f"Bearer {session_hex}"}
        self._path_prefix = path_prefix
        if client is not None:
            self._http_get = client.get
            self._http_post = client.post
        else:
            self._http_get = lambda path, **kw: requests.get(f"{base_url}{path}", **kw)
            self._http_post = lambda path, **kw: requests.post(f"{base_url}{path}", **kw)

    # -- endpoint hooks -- #

    def _download_endpoint(self, cloud_path: str) -> Tuple[str, dict]:
        raise NotImplementedError

    def _transform_download(self, data: bytes) -> bytes:
        return data

    # -- status classification -- #

    @staticmethod
    def _detail(resp) -> str:
        try:
            body = resp.json()
        except Exception:
            return resp.text
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("error") or body)
        return str(body)

    def _classify(self, resp, cloud_path: str) -> StoreError:
        detail = self._detail(resp)
        if resp.status_code == 404:
            return ObjectNotFoundError(cloud_path)
        if resp.status_code == 401:
            return StoreAuthenticationError(f"{cloud_path}: {detail}")
        if resp.status_code == 403:
            return StoreAuthorizationError(f"{cloud_path}: {detail}")
        try:
            error_code = resp.json().get("error")
        except Exception:
            error_code = None
        if resp.status_code == 409 and error_code == "cas_conflict":
            return CasConflictError(f"{cloud_path}: {detail}")
        return StoreProviderError(f"{cloud_path}: HTTP {resp.status_code}: {detail}")

    def _send(self, send, *args, **kwargs):
        try:
            return send(*args, **kwargs)
        except StoreError:
            raise
        except Exception as exc:
            raise StoreTransportError(f"request failed: {exc}") from exc

    def _download(self, cloud_path: str) -> Tuple[bytes, Optional[str]]:
        endpoint, params = self._download_endpoint(cloud_path)
        resp = self._send(self._http_get, endpoint, params=params, headers=self._auth)
        if resp.status_code != 200:
            raise self._classify(resp, cloud_path)
        try:
            body = resp.json()
            data = base64.b64decode(body["data"])
            etag = body.get("etag")
        except Exception as exc:
            raise MalformedStoreResponseError(
                f"{cloud_path}: unreadable Hub response: {exc}"
            ) from exc
        return self._transform_download(data), etag

    # -- reads -- #

    def get_latest_link(self) -> Tuple[bytes, Optional[str]]:
        return self._download(LATEST_LINK_PATH)

    def get_link(self, link_uid: str) -> bytes:
        return self._download(link_path(link_uid))[0]

    def download_bundle(self, bundle_uid: str, local_path) -> None:
        data, _etag = self._download(bundle_path(bundle_uid))
        with open(local_path, "wb") as handle:
            handle.write(data)


class SmallSeaStore(_HubStore):
    """The session's own cloud storage, reached through the Hub.

    path_prefix namespaces one Cod Sync chain within a bucket, so several
    chains and the Hub's own signals.yaml can share it.
    """

    def __init__(
        self,
        session_hex: str,
        base_url: str = "http://localhost:11437",
        client=None,
        path_prefix: str = "",
    ):
        super().__init__(session_hex, base_url, client=client, path_prefix=path_prefix)

    def _download_endpoint(self, cloud_path: str):
        return "/cloud_file", {"path": self._path_prefix + cloud_path}

    def _upload(
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
        resp = self._send(self._http_post, "/cloud_file", json=payload, headers=self._auth)
        if resp.status_code != 200:
            raise self._classify(resp, cloud_path)
        try:
            return resp.json().get("etag")
        except Exception as exc:
            raise MalformedStoreResponseError(
                f"{cloud_path}: unreadable Hub response: {exc}"
            ) from exc

    def put_bundle(self, bundle_uid: str, local_path) -> None:
        with open(local_path, "rb") as handle:
            self._upload(bundle_path(bundle_uid), handle.read(), CREATE_ONLY)

    def put_link(self, link_uid: str, data: bytes) -> None:
        self._upload(link_path(link_uid), data, CREATE_ONLY)

    def put_latest_link(
        self, data: bytes, expected_etag: Optional[str], link_uid: Optional[str] = None
    ) -> Optional[str]:
        # notify=True tells the Hub to bump signals.yaml once the head moves.
        try:
            return self._upload(
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
    """Read-only view of a teammate's chain, proxied by the Hub.

    The Hub authenticates the session, resolves the peer's cloud location, and
    returns the bytes, so the client never talks to cloud storage itself.
    """

    def __init__(
        self,
        session_hex: str,
        teammate_id_hex: str,
        base_url: str = "http://localhost:11437",
        client=None,
        path_prefix: str = "",
    ):
        super().__init__(session_hex, base_url, client=client, path_prefix=path_prefix)
        self.teammate_id_hex = teammate_id_hex

    def _download_endpoint(self, cloud_path: str):
        return "/peer_cloud_file", {
            "teammate_id": self.teammate_id_hex,
            "path": self._path_prefix + cloud_path,
        }


class ExplicitProxyStore(_HubStore):
    """Read-only view of a chain at explicit cloud coordinates.

    Used during invitation acceptance, when the inviter's repository must be
    read before any peer relationship exists. Requires a NoteToSelf session.
    """

    def __init__(
        self,
        session_hex: str,
        protocol: str,
        url: str,
        bucket: str,
        base_url: str = "http://localhost:11437",
        client=None,
        download_transform=None,
    ):
        super().__init__(session_hex, base_url, client=client)
        self._protocol = protocol
        self._url = url
        self._bucket = bucket
        self._download_transform = download_transform

    def _download_endpoint(self, cloud_path: str):
        return "/cloud_proxy", {
            "protocol": self._protocol,
            "url": self._url,
            "bucket": self._bucket,
            "path": cloud_path,
        }

    def _transform_download(self, data: bytes) -> bytes:
        if self._download_transform is None:
            return data
        return self._download_transform(data)


class BootstrapProxyStore(_HubStore):
    """Read-only view scoped to the cloud descriptor bound to a bootstrap token."""

    def __init__(
        self, session_hex: str, base_url: str = "http://localhost:11437", client=None
    ):
        super().__init__(session_hex, base_url, client=client)

    def _download_endpoint(self, cloud_path: str):
        return "/bootstrap/cloud_file", {"path": cloud_path}
