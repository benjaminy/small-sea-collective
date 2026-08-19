"""Shared fixtures-adjacent helpers for the Cod Sync tests.

Not conftest.py: several packages have one, and pytest imports them all under
the same `conftest` module name when their suites run together.
"""

import pathlib

from cod_sync.protocol import MAIN_REF, CodSync
from cod_sync.repo import Repo
from cod_sync.store import LocalFolderStore


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


def make_cod_sync(repo: Repo, store) -> CodSync:
    return CodSync(repo, store)


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
