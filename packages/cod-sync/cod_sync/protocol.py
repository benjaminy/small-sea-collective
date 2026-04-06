# Cod Sync

import base64
import hashlib
import io
import logging
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile

import requests
import yaml

program_title = "Cod Sync protocol Git remote helper work-a-like"

logger = logging.getLogger("cod_sync")

COD_SYNC_VERSION = "1.0.0"


# --- Link signing and verification ---


def canonical_link_bytes(link_ids, branches, bundles, supplement):
    """Produce the canonical byte string that is signed for a link.

    Covers link_ids, branches, bundles, and supplement (excluding the
    'signatures' key if present). Deterministic YAML serialization.

    Normalizes bundle prerequisites to flat list form (the wire format)
    since read_link_blob converts them to dicts.
    """
    supp_without_sigs = {k: v for k, v in supplement.items() if k != "signatures"}
    # Normalize: read_link_blob converts prerequisites from list to dict;
    # canonical form always uses the flat list.
    normalized_bundles = []
    for bundle in bundles:
        prereqs = bundle[1]
        if isinstance(prereqs, dict):
            flat = []
            for k, v in sorted(prereqs.items()):
                flat.extend([k, v])
            normalized_bundles.append([bundle[0], flat])
        else:
            normalized_bundles.append(bundle)
    signable = [link_ids, branches, normalized_bundles, supp_without_sigs]
    return yaml.dump(signable, default_flow_style=False, sort_keys=True).encode("utf-8")


def sign_link(private_key_bytes, canonical_bytes):
    """Sign canonical link bytes with an Ed25519 private key.

    private_key_bytes: 32-byte Ed25519 private key (raw format).
    Returns a base64-encoded signature string.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    signature = key.sign(canonical_bytes)
    return base64.b64encode(signature).decode()


def verify_link_signature(public_key_bytes, signature_b64, canonical_bytes):
    """Verify an Ed25519 signature on canonical link bytes.

    public_key_bytes: 32-byte Ed25519 public key (raw format).
    signature_b64: base64-encoded signature string.
    Returns True if valid, False otherwise.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature

    key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    signature = base64.b64decode(signature_b64)
    try:
        key.verify(signature, canonical_bytes)
        return True
    except InvalidSignature:
        return False


class CasConflictError(Exception):
    """Raised when a compare-and-swap write fails due to a concurrent update."""

    pass


def gitCmd(git_params, raise_on_error=True):
    git_cmd = ["git"] + git_params
    result = subprocess.run(git_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        exn = GitCmdFailed(git_params, result.returncode, result.stdout, result.stderr)
        if raise_on_error:
            raise exn
        else:
            logger.debug(str(exn))
    return result


class CodSync:

    def __init__(self, remote_name, bundle_tmp_dir=None, repo_dir=None):
        self.remote_name = remote_name
        self.url = None
        self._repo_dir = str(repo_dir) if repo_dir is not None else None
        if self._repo_dir is not None:
            _rd = self._repo_dir
            self.gitCmd = lambda params, raise_on_error=True: gitCmd(["-C", _rd] + params, raise_on_error=raise_on_error)
        else:
            self.gitCmd = gitCmd
        self._bundle_tmp_dir = bundle_tmp_dir

    def add_remote(self, url, dotdotdot):
        """Add a Cod Sync remote

        :param remote: The nickname for the remote
        :param url: The URL for the remote. (This should not include 'codsync:')

        Currently the only supported URL schema is file://
        In the fullness of time the idea is to support googledrive: , etc

        This function adds 2 remotes to the underlying repo:
        - One with the actual remote URL
          - This one is never directly `git fetch`d or `git push`d or whatever
        - One with a temp bundle name
          - When pulling from a remote, the bundle is copied here and `git fetch`d
        """

        self.gitCmd(["remote", "add", self.remote_name, f"codsync:{url}"])
        logger.debug(f"Added remote '{self.remote_name}' ({url})")

        [bundle_remote, path] = self.bundle_tmp()
        self.gitCmd(["remote", "add", bundle_remote, f"{path}/fetch.bundle"])
        logger.debug(f"Added remote '{bundle_remote}' ({path})")

    def remove_remote(self, dotdotdot):
        """Remove a Cod Sync remote"""

        result1 = self.gitCmd(["remote", "remove", self.remote_name], False)
        if result1.returncode == 0:
            logger.debug(f"Removed remote '{self.remote_name}'")

        [bundle_remote, _] = self.bundle_tmp()
        result2 = self.gitCmd(["remote", "remove", bundle_remote])
        if result2.returncode == 0:
            logger.debug(f"Removed remote '{bundle_remote}'")

        if result1.returncode != 0:
            return result1.returncode

        if result2.returncode != 0:
            return result2.returncode

        return 0

    def initialize_existing_remote(self):
        """git remote get-url `remote_name`
        with some error checking. Plus strip the 'codsync:' prefix,
        """
        self.url = None
        result = self.gitCmd(["remote", "get-url", self.remote_name], False)
        if result.returncode != 0:
            return

        remote_url = result.stdout.strip()

        if not remote_url.startswith("codsync:"):
            logger.warning(f"Wrong remote protocol '{remote_url}' ({program_title})")
            return

        # Strip 'codsync:'
        self.url = remote_url[8:]

    def push_to_remote(self, branches, signing_key=None, member_id=None):
        logger.debug(f"push_to_remote {self.remote_name} {branches}")

        bundle_uid = CodSync.token_hex(8)
        [_, path_tmp] = self.bundle_tmp()
        os.makedirs(path_tmp, exist_ok=True)
        bundle_path_tmp = f"{path_tmp}/B-{bundle_uid}.bundle"

        result = self.remote.get_latest_link()
        if result is None:
            latest_link = None
            etag = None
        else:
            latest_link, etag = result

        if latest_link is None:
            link_uid = "initial-snapshot"
            link_uid_prev = "initial-snapshot"
            prerequisites = {"main": "initial-snapshot"}
            bundle_spec = "main"
            #     return 0

        else:
            [link_ids, branches, bundles, supp_data] = latest_link
            link_uid = CodSync.token_hex(8)
            link_uid_prev = link_ids[0]
            assert len(branches) == 1
            assert len(link_ids) > 0
            branch = branches[0]
            assert branch[0] == "main"
            prerequisites = {"main": branch[1]}
            tag = f"codsync_temp_tag_{'main'}"
            tag_result = self.gitCmd(["tag", tag, branch[1]], raise_on_error=False)
            if tag_result.returncode != 0:
                # Remote SHA not present locally (e.g. fresh clone pushing to existing bucket).
                # Fall back to initial-snapshot: send full history.
                latest_link = None
                link_uid = "initial-snapshot"
                link_uid_prev = "initial-snapshot"
                prerequisites = {"main": "initial-snapshot"}
                bundle_spec = "main"
            else:
                bundle_spec = f"{tag}..main"

        self.gitCmd(["bundle", "create", bundle_path_tmp, bundle_spec])

        if latest_link is not None:
            self.gitCmd(["tag", "-d", tag])

        blob = self.build_link_blob(
            link_uid, link_uid_prev, bundle_uid, prerequisites,
            signing_key=signing_key, member_id=member_id,
        )
        logger.debug(f"pushing link {link_uid} bundle {bundle_path_tmp}")
        return self.remote.upload_latest_link(
            link_uid, blob, bundle_uid, bundle_path_tmp, expected_etag=etag
        )

    def build_link_blob(self, new_link_uid, prev_link_uid, bundle_uid, prerequisites,
                        signing_key=None, member_id=None):
        link_ids = [new_link_uid, prev_link_uid]
        branch_names = self.get_branches()
        branches = []
        for branch in branch_names:
            branches.append([branch, self.get_branch_head_sha(branch)])
        logger.debug(f"branches {branches}")
        bundles = [[bundle_uid, ["main", prerequisites["main"]]]]
        supplement = {"cod_version": COD_SYNC_VERSION}

        if signing_key is not None and member_id is not None:
            signable = canonical_link_bytes(link_ids, branches, bundles, supplement)
            signature = sign_link(signing_key, signable)
            supplement["signatures"] = {member_id: signature}

        return [link_ids, branches, bundles, supplement]

    def clone_from_remote(self, url, remote=None):
        logger.debug(f"clone_from_remote {self.remote_name} {url}")

        git_cmd = ["git", "rev-parse", "--show-toplevel"]
        result = subprocess.run(git_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.warning(
                f"clone_from_remote: already in a repo '{os.getcwd()}' '{result.stdout.strip()}'"
            )
            return -1

        if remote is not None:
            self.remote = remote
        else:
            self.remote = CodSyncRemote.init(url)
        result = self.remote.get_latest_link()
        if result is None:
            latest_link = None
        else:
            latest_link, _etag = result
        if latest_link == None:
            logger.warning("clone_from_remote: no latest link found")
            return -1

        # Walk back through the link chain to collect all links from initial to latest
        chain = [latest_link]
        current = latest_link
        while current[0][0] != "initial-snapshot":
            prev_link_uid = current[0][1]
            prev_link = self.remote.get_link(prev_link_uid)
            if prev_link is None:
                logger.warning(f"clone_from_remote: broken chain at {prev_link_uid}")
                return -1
            chain.append(prev_link)
            current = prev_link

        # chain is [latest, ..., initial] — reverse to get [initial, ..., latest]
        chain.reverse()

        # Clone from the initial snapshot bundle
        initial_link = chain[0]
        if len(initial_link[2]) != 1:
            logger.warning(f"clone_from_remote: unexpected initial link bundles {initial_link[2]}")
            return -1
        initial_bundle_uid = initial_link[2][0][0]

        with tempfile.TemporaryDirectory() as bundle_temp_dir:
            bundle_path = f"{bundle_temp_dir}/clone.bundle"
            self.remote.download_bundle(initial_bundle_uid, bundle_path)
            self.gitCmd(["clone", bundle_path, "."])

        self.gitCmd(["checkout", "main"])

        self.add_remote(url, [])

        # Apply remaining incremental bundles
        for link in chain[1:]:
            result = self.fetch_chain(link, ["main"], True)
            if result != 0:
                return result

            [tmp_remote, _] = self.bundle_tmp()
            self.gitCmd(["merge", f"{tmp_remote}/main"])

        logger.info(f"clone_from_remote: done")
        return 0

    def fetch_from_remote(self, branches, pin_to_ref=None):
        logger.debug(f"fetch_from_remote {self.remote_name} {branches}")
        result = self.remote.get_latest_link()
        if result is None:
            latest_link = None
        else:
            latest_link, _etag = result

        if latest_link == None:
            logger.warning(f"fetch_from_remote: no latest link found")
            return None

        result = self.fetch_chain(latest_link, branches, False)
        if result != 0:
            return None

        branch_name = branches[0]
        fetched_sha = self._link_branch_sha(latest_link, branch_name)
        if fetched_sha is None:
            return None

        if pin_to_ref is not None:
            self.gitCmd(["update-ref", pin_to_ref, fetched_sha])

        return fetched_sha

    def fetch_chain(self, link, branches, doing_clone):
        [link_ids, branches, bundles, supp_data] = link

        if len(bundles) != 1:
            logger.warning(f"fetch_chain: unexpected bundle count {bundles}")
            return -1

        bundle = bundles[0]
        bundle_uid = bundle[0]
        bundle_prereqs = bundle[1]
        if len(bundle_prereqs) != 1 or "main" not in bundle_prereqs.keys():
            logger.warning(f"fetch_chain: unexpected bundle prerequisites {bundles}")
            return -1

        prereq = bundle_prereqs["main"]
        if prereq == "initial-snapshot":
            logger.debug("fetch_chain: initial snapshot")
        else:
            if doing_clone:
                follow_chain = True
            else:
                result = self.gitCmd(["cat-file", "-t", prereq], False)
                follow_chain = result.stdout.strip() != "commit"

            if follow_chain:
                next_link = self.remote.get_link(link_ids[1])
                result = self.fetch_chain(next_link, branches, doing_clone)
                if result != 0:
                    return result

        [tmp_remote, path_tmp] = self.bundle_tmp()
        os.makedirs(path_tmp, exist_ok=True)

        bundle_path = f"{path_tmp}/fetch.bundle"
        self.remote.download_bundle(bundle_uid, bundle_path)

        self.gitCmd(["bundle", "verify", bundle_path])
        self._ensure_bundle_remote()
        self.gitCmd(["fetch", tmp_remote])

        return 0

    def _link_branch_sha(self, link, branch_name):
        _link_ids, branches, _bundles, _supp_data = link
        for branch, sha in branches:
            if branch == branch_name:
                return sha
        logger.warning(f"_link_branch_sha: branch not found in link: {branch_name}")
        return None

    def merge_from_remote(self, branches):
        logger.debug(f"merge_from_remote {self.remote_name} {branches}")
        branch = branches[0]

        [tmp_remote, _] = self.bundle_tmp()

        result = self.gitCmd(["merge", f"{tmp_remote}/{branch}"], raise_on_error=False)
        return result.returncode

    def merge_from_ref(self, ref_name):
        logger.debug(f"merge_from_ref {self.remote_name} {ref_name}")
        result = self.gitCmd(["merge", ref_name], raise_on_error=False)
        return result.returncode

    def get_branches(self):
        """git for-each-ref --format=%(refname:short) refs/heads/
        with error checking
        """
        result = self.gitCmd(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads/"], False
        )
        # cwd=repo_path,

        if result.returncode == 0:
            return result.stdout.splitlines()

        return []

    def get_branch_head_sha(self, branch):
        logger.debug(f"get_branch_head_sha {branch}")
        result = self.gitCmd(["rev-parse", f"refs/heads/{branch}"], False)
        # cwd=repo_path,

        if result.returncode == 0:
            return result.stdout.strip()

        return "0xdeadbeef"

    def token_hex(num_bytes):
        return "".join(f"{b:02x}" for b in secrets.token_bytes(num_bytes))

    def change_to_root_git_dir(self):
        """cd $( git rev-parse --show-toplevel )
        with some error checking
        """
        result = self.gitCmd(["rev-parse", "--show-toplevel"])
        git_dir = result.stdout.strip()
        os.chdir(git_dir)
        if pathlib.Path(git_dir).resolve() == pathlib.Path(os.getcwd()).resolve():
            return 0
        logger.warning(
            f"change_to_root_git_dir: unexpected chdir result {result.stdout} {os.getcwd()}"
        )
        return -1

    def _ensure_bundle_remote(self):
        [bundle_remote, path] = self.bundle_tmp()
        result = self.gitCmd(["remote", "get-url", bundle_remote], raise_on_error=False)
        if result.returncode != 0:
            os.makedirs(path, exist_ok=True)
            self.gitCmd(["remote", "add", bundle_remote, f"{path}/fetch.bundle"])

    def bundle_tmp(self):
        if self._bundle_tmp_dir is not None:
            base = str(self._bundle_tmp_dir)
        elif self._repo_dir is not None:
            base = str(pathlib.Path(self._repo_dir) / ".codsync-bundle-tmp")
        else:
            base = f"./.codsync-bundle-tmp"
        return [
            f"{self.remote_name}-codsync-bundle-tmp",
            f"{base}/{self.remote_name}",
        ]


class GitCmdFailed(Exception):
    def __init__(self, params, exit_code, out, err):
        self.params = params
        self.exit_code = exit_code
        self.out = out
        self.err = err

    def __str__(self):
        return f"ERROR. git cmd failed. `git {' '.join(self.params)}` => {self.exit_code}. o:'{self.out}' e:'{self.err}'"


class CodSyncRemote:
    """Abstract class for different kinds of remotes (Google Drive, etc)"""

    @staticmethod
    def init(url):
        smallsea_prefix = "smallsea://"
        if url.startswith("file://"):
            return LocalFolderRemote(url[7:].strip())
        if url.startswith(smallsea_prefix):
            remainder = url[len(smallsea_prefix) :].strip()
            # remainder is host:port/SESSION_HEX
            slash_pos = remainder.find("/")
            if slash_pos < 0:
                raise ValueError(
                    f"Invalid smallsea URL, expected smallsea://host:port/SESSION_HEX, got '{url}'"
                )
            host_port = remainder[:slash_pos]
            session_hex = remainder[slash_pos + 1 :]
            return SmallSeaRemote(session_hex, base_url=f"http://{host_port}")

        raise NotImplementedError(f"Unsupported Cod Sync cloud protocol. '{url}'")

    def read_link_blob(self, yaml_strm):
        parsed_data = yaml.load(yaml_strm, Loader=yaml.FullLoader)
        link_ids = parsed_data[0]
        branches = parsed_data[1]
        bundles = parsed_data[2]
        for bundle in bundles:
            ps = bundle[1]
            bundle[1] = dict([(ps[i], ps[i + 1]) for i in range(0, len(ps), 2)])
        if len(parsed_data) > 3:
            supp_data = parsed_data[3]
        else:
            supp_data = {}

        # Version compatibility check
        link_version = supp_data.get("cod_version", "0.0.0")
        link_major = int(link_version.split(".")[0])
        reader_major = int(COD_SYNC_VERSION.split(".")[0])
        if link_major > reader_major:
            raise ValueError(
                f"Link format version {link_version} is incompatible with this reader "
                f"(supports up to major version {reader_major}). Please upgrade Cod Sync."
            )

        return [link_ids, branches, bundles, supp_data]


class LocalFolderRemote(CodSyncRemote):
    """Mostly for debugging purposes. Pretend a local folder is a cloud location."""

    def __init__(self, path):
        self.path = None

        if not os.path.isdir(path):
            logger.warning(f"LocalFolderRemote: not a directory '{path}'")
            return -1

        self.path = path

    @staticmethod
    def _file_etag(path):
        """Compute an etag (MD5 hex digest) for a file's content."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def upload_latest_link(
        self, link_uid, blob, bundle_uid, local_bundle_path, expected_etag=None
    ):
        path_bundle = f"{self.path}{os.path.sep}B-{bundle_uid}.bundle"
        shutil.copy(local_bundle_path, path_bundle)

        path_latest = f"{self.path}{os.path.sep}latest-link.yaml"

        # CAS check for latest-link.yaml
        if expected_etag is not None:
            if not os.path.exists(path_latest):
                raise CasConflictError(
                    "expected existing file but latest-link.yaml does not exist"
                )
            current_etag = self._file_etag(path_latest)
            if current_etag != expected_etag:
                raise CasConflictError(
                    f"CAS conflict on latest-link.yaml: expected etag {expected_etag}, got {current_etag}"
                )
        elif os.path.exists(path_latest) and expected_etag is None:
            # First push should not have an etag; subsequent pushes should.
            # We allow None for backward compat but the caller should pass etags.
            pass

        with open(path_latest, "w", encoding="utf-8") as link_strm:
            yaml.dump(blob, link_strm, default_flow_style=False)

        path_uid = f"{self.path}{os.path.sep}L-{link_uid}.yaml"
        with open(path_uid, "w", encoding="utf-8") as link_strm:
            yaml.dump(blob, link_strm, default_flow_style=False)

    def get_link(self, uid):
        if uid == "latest-link":
            path_link = f"{self.path}{os.path.sep}latest-link.yaml"
        else:
            path_link = f"{self.path}{os.path.sep}L-{uid}.yaml"

        if not os.path.exists(path_link):
            logger.debug(f"LocalFolderRemote.get_link: not found {path_link}")
            return None

        with open(path_link, "r") as link_file_strm:
            link = self.read_link_blob(link_file_strm)

        if uid == "latest-link":
            etag = self._file_etag(path_link)
            return (link, etag)
        return link

    def get_latest_link(self):
        return self.get_link("latest-link")

    def download_bundle(self, bundle_uid, local_bundle_path):
        path_bundle = f"{self.path}{os.path.sep}B-{bundle_uid}.bundle"
        shutil.copy(path_bundle, local_bundle_path)


class SmallSeaRemote(CodSyncRemote):
    """Hub-backed cloud storage remote.

    Talks to the hub's POST /cloud_file and GET /cloud_file endpoints.

    path_prefix is prepended to every cloud path, allowing multiple
    CodSync repos to coexist in the same bucket without namespace collision.
    For example, path_prefix="vault/registry/" puts registry bundles under
    that prefix while signals.yaml and team-sync data stay at the root.
    """

    def __init__(self, session_hex, base_url="http://localhost:11437", client=None,
                 path_prefix=""):
        self.session_hex = session_hex
        self._auth = {"Authorization": f"Bearer {session_hex}"}
        self._path_prefix = path_prefix

        if client is not None:
            self._post = client.post
            self._get = client.get
        else:
            self._post = lambda path, **kw: requests.post(f"{base_url}{path}", **kw)
            self._get = lambda path, **kw: requests.get(f"{base_url}{path}", **kw)

    def _upload(self, cloud_path, data_bytes, expected_etag=None, notify=False):
        payload = {
            "path": self._path_prefix + cloud_path,
            "data": base64.b64encode(data_bytes).decode(),
        }
        if expected_etag is not None:
            payload["expected_etag"] = expected_etag
        if notify:
            payload["notify"] = True
        resp = self._post("/cloud_file", json=payload, headers=self._auth)
        if resp.status_code == 409:
            raise CasConflictError(f"CAS conflict uploading {cloud_path}")
        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(
                f"cloud upload failed ({resp.status_code}): {detail}"
            )
        return resp

    def _download(self, cloud_path):
        resp = self._get(
            "/cloud_file",
            params={"path": self._path_prefix + cloud_path},
            headers=self._auth,
        )
        if resp.status_code != 200:
            return (None, None)
        body = resp.json()
        data = base64.b64decode(body["data"])
        etag = body.get("etag")
        return (data, etag)

    def upload_latest_link(
        self, link_uid, blob, bundle_uid, local_bundle_path, expected_etag=None
    ):
        # 1. Upload bundle
        with open(local_bundle_path, "rb") as f:
            bundle_bytes = f.read()
        self._upload(f"B-{bundle_uid}.bundle", bundle_bytes)

        # 2. Serialize link YAML
        link_yaml = yaml.dump(blob, default_flow_style=False).encode("utf-8")

        # 3. Upload L-{link_uid}.yaml, then latest-link.yaml with notify=True
        #    notify signals the Hub to bump signals.yaml after this write.
        self._upload(f"L-{link_uid}.yaml", link_yaml)
        self._upload("latest-link.yaml", link_yaml, expected_etag=expected_etag, notify=True)

    def get_link(self, uid):
        if uid == "latest-link":
            cloud_path = "latest-link.yaml"
        else:
            cloud_path = f"L-{uid}.yaml"

        data, etag = self._download(cloud_path)
        if data is None:
            return None

        link = self.read_link_blob(io.BytesIO(data))

        if uid == "latest-link":
            return (link, etag)
        return link

    def get_latest_link(self):
        return self.get_link("latest-link")

    def download_bundle(self, bundle_uid, local_bundle_path):
        data, _ = self._download(f"B-{bundle_uid}.bundle")
        if data is None:
            raise RuntimeError(f"Failed to download bundle B-{bundle_uid}.bundle")
        with open(local_bundle_path, "wb") as f:
            f.write(data)


class PeerSmallSeaRemote(CodSyncRemote):
    """Read-only remote that fetches a peer's cloud files via the Hub proxy endpoint.

    The Hub authenticates the session, looks up the peer's cloud URL, and proxies
    the data back — so the client never talks directly to cloud storage.

    path_prefix is prepended to every cloud path, matching the prefix used
    when the peer pushed the content via SmallSeaRemote.
    """

    def __init__(self, session_hex, member_id_hex, base_url="http://localhost:11437",
                 client=None, path_prefix=""):
        self.session_hex = session_hex
        self.member_id_hex = member_id_hex
        self._auth = {"Authorization": f"Bearer {session_hex}"}
        self._path_prefix = path_prefix

        if client is not None:
            self._get = client.get
        else:
            self._get = lambda path, **kw: requests.get(f"{base_url}{path}", **kw)

    def _download(self, cloud_path):
        resp = self._get(
            "/peer_cloud_file",
            params={"member_id": self.member_id_hex, "path": self._path_prefix + cloud_path},
            headers=self._auth,
        )
        if resp.status_code != 200:
            return (None, None)
        body = resp.json()
        data = base64.b64decode(body["data"])
        etag = body.get("etag")
        return (data, etag)

    def upload_latest_link(self, *args, **kwargs):
        raise NotImplementedError("PeerSmallSeaRemote is read-only")

    def get_link(self, uid):
        if uid == "latest-link":
            cloud_path = "latest-link.yaml"
        else:
            cloud_path = f"L-{uid}.yaml"

        data, etag = self._download(cloud_path)
        if data is None:
            return None

        link = self.read_link_blob(io.BytesIO(data))

        if uid == "latest-link":
            return (link, etag)
        return link

    def get_latest_link(self):
        return self.get_link("latest-link")

    def download_bundle(self, bundle_uid, local_bundle_path):
        data, _ = self._download(f"B-{bundle_uid}.bundle")
        if data is None:
            raise RuntimeError(f"Failed to download bundle B-{bundle_uid}.bundle from peer")
        with open(local_bundle_path, "wb") as f:
            f.write(data)


class ExplicitProxyRemote(CodSyncRemote):
    """Read-only remote that fetches cloud files via the Hub's /cloud_proxy endpoint.

    Passes explicit cloud coordinates (protocol, url, bucket) rather than looking
    them up from a peer row. Used during invitation acceptance to clone the
    inviter's team repo before any peer relationship exists.

    Requires a NoteToSelf session token.
    """

    def __init__(self, session_hex, protocol, url, bucket,
                 base_url="http://localhost:11437", client=None,
                 download_transform=None):
        self.session_hex = session_hex
        self._auth = {"Authorization": f"Bearer {session_hex}"}
        self._protocol = protocol
        self._url = url
        self._bucket = bucket
        self._download_transform = download_transform

        if client is not None:
            self._get = client.get
        else:
            self._get = lambda path, **kw: requests.get(f"{base_url}{path}", **kw)

    def _download(self, cloud_path):
        resp = self._get(
            "/cloud_proxy",
            params={
                "protocol": self._protocol,
                "url": self._url,
                "bucket": self._bucket,
                "path": cloud_path,
            },
            headers=self._auth,
        )
        if resp.status_code != 200:
            return (None, None)
        body = resp.json()
        data = base64.b64decode(body["data"])
        if self._download_transform is not None:
            data = self._download_transform(data)
        etag = body.get("etag")
        return (data, etag)

    def upload_latest_link(self, *args, **kwargs):
        raise NotImplementedError("ExplicitProxyRemote is read-only")

    def get_link(self, uid):
        if uid == "latest-link":
            cloud_path = "latest-link.yaml"
        else:
            cloud_path = f"L-{uid}.yaml"

        data, etag = self._download(cloud_path)
        if data is None:
            return None

        link = self.read_link_blob(io.BytesIO(data))

        if uid == "latest-link":
            return (link, etag)
        return link

    def get_latest_link(self):
        return self.get_link("latest-link")

    def download_bundle(self, bundle_uid, local_bundle_path):
        data, _ = self._download(f"B-{bundle_uid}.bundle")
        if data is None:
            raise RuntimeError(f"Failed to download bundle B-{bundle_uid}.bundle via proxy")
        with open(local_bundle_path, "wb") as f:
            f.write(data)


if __name__ == "__main__":
    logger.error("This file contains no main")
