"""Shared fixtures-adjacent helpers for the Cod Sync tests.

Not conftest.py: several packages have one, and pytest imports them all under
the same `conftest` module name when their suites run together.
"""

import asyncio
import pathlib
import subprocess

import pytest

import cod_sync.aio.protocol as aio_protocol
from cod_sync.aio.repo import Repo as AioRepo
from cod_sync.aio.verify import SshCommitVerifier as AioSshCommitVerifier
from cod_sync.protocol import MAIN_REF, CodSync
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore
from cod_sync.verify import SshCommitVerifier


def make_repo(path, name="alice") -> Repo:
    """Create a repo with a work tree at path, ready to commit."""
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path / ".git").with_work_tree(path)
    repo.config("user.email", f"{name}@test")
    repo.config("user.name", name)
    return repo


def commit_file(repo: Repo, name: str, content: str, message=None) -> str:
    """Write, stage, and commit one file. Returns the new SHA."""
    (repo.work_tree / name).write_text(content)
    repo.stage([name])
    return repo.commit(message or f"write {name}")


def make_store(path) -> LocalFolderStore:
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return LocalFolderStore(str(path))


#: Which CodSync make_cod_sync builds: "sync", or "aio" for the async one.
#: The both_cod_syncs fixture sets it.
COD_SYNC_FLAVOR = "sync"


def make_cod_sync(repo: Repo, store, verifier=None):
    if COD_SYNC_FLAVOR == "aio":
        return BlockingAioCodSync(repo, store, verifier)
    return CodSync(repo, store, verifier=verifier)


class BlockingAioCodSync:
    """The async CodSync behind the sync CodSync's methods.

    This lets the sync protocol micro tests check the async CodSync too.
    Their repos, stores and verifiers are sync, so each is wrapped: the async
    Repo opens the same git directory, and the store and verifier wrappers
    call the sync objects, looking methods up at call time so a test's
    monkeypatching still applies.
    """

    def __init__(self, repo: Repo, store, verifier=None):
        self.repo = repo
        self._aio = aio_protocol.CodSync(
            AioRepo(repo.git_dir, repo.work_tree, env=repo.env), None
        )
        self.store = store
        self.verifier = verifier

    # Tests swap a CodSync's store or verifier between calls.
    @property
    def store(self):
        return self._store

    @store.setter
    def store(self, store):
        self._store = store
        self._aio.store = _AsyncStore(store)

    @property
    def verifier(self):
        return self._verifier

    @verifier.setter
    def verifier(self, verifier):
        self._verifier = verifier
        self._aio.verifier = None if verifier is None else _AsyncVerifier(verifier, self.repo)

    def publish(self, *args, **kwargs):
        return asyncio.run(self._aio.publish(*args, **kwargs))

    def fetch(self, *args, **kwargs):
        return asyncio.run(self._aio.fetch(*args, **kwargs))


class _AsyncStore:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            return getattr(self.inner, name)(*args, **kwargs)
        return call


class _AsyncVerifier:
    def __init__(self, inner, repo: Repo):
        self.inner = inner
        self.repo = repo

    async def verify_history(self, aio_repo, head):
        if type(self.inner) is SshCommitVerifier:
            # The async twin, so the protocol tests check it as well.
            verifier = AioSshCommitVerifier(self.inner.allowed_keys)
            return await verifier.verify_history(aio_repo, head)
        self.inner.verify_history(self.repo, head)


@pytest.fixture
def isolated_git_config(monkeypatch):
    """Hide the developer's own git config and ssh-agent from a signing test.

    Import this into a signing or verification module as an autouse fixture.
    Without it a developer's own signing config silently signs the unsigned
    control, so the negative controls pass for the wrong reason on a laptop and
    fail in CI.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


def make_ssh_key(directory, name):
    """Generate a fresh Ed25519 SSH key pair. Returns the private key path."""
    path = pathlib.Path(directory) / f"{name}_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def public_key_text(key_path):
    return pathlib.Path(f"{key_path}.pub").read_text().strip()


def working_tree_files(repo: Repo):
    """Return {path: content} for every tracked file."""
    result = repo._run(["ls-files"])
    return {
        name: (repo.work_tree / name).read_text()
        for name in result.stdout.strip().splitlines()
        if name
    }


def all_refs(repo: Repo):
    """Return {refname: sha} for every ref in the repository."""
    result = repo._run(["for-each-ref", "--format=%(refname) %(objectname)"])
    refs = {}
    for line in result.stdout.splitlines():
        name, _, sha = line.partition(" ")
        if name:
            refs[name] = sha
    return refs


def assert_no_scratch(repo: Repo):
    """No Cod Sync scratch path may appear under a work tree or the git dir."""
    roots = [repo.git_dir]
    if repo.work_tree is not None:
        roots.append(repo.work_tree)
    for root in roots:
        stray = [
            p
            for p in pathlib.Path(root).rglob("*codsync*")
            if "cod-sync-test-" not in str(p)
        ]
        assert stray == [], f"Cod Sync scratch left behind: {stray}"


class CountingStore:
    """Read-through wrapper that counts what an operation actually fetched.

    Exists so "each bundle is downloaded once" is a measurement rather than an
    assumption; the old chain walk was quadratic and no test noticed.
    """

    def __init__(self, inner):
        self.inner = inner
        self.link_reads = []
        self.bundle_reads = []
        self.latest_reads = 0

    def get_latest_link(self):
        self.latest_reads += 1
        return self.inner.get_latest_link()

    def get_link(self, link_uid):
        self.link_reads.append(link_uid)
        return self.inner.get_link(link_uid)

    def download_bundle(self, bundle_uid, local_path):
        self.bundle_reads.append(bundle_uid)
        return self.inner.download_bundle(bundle_uid, local_path)

    def put_bundle(self, bundle_uid, local_path):
        return self.inner.put_bundle(bundle_uid, local_path)

    def put_link(self, link_uid, data):
        return self.inner.put_link(link_uid, data)

    def put_latest_link(self, data, expected_etag, link_uid=None):
        return self.inner.put_latest_link(data, expected_etag, link_uid=link_uid)
