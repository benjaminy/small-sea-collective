# INCOMPLETE SKETCH — sync orchestration
#
# This was started in the hub backend but belongs here: the hub provides
# upload/download primitives for cloud storage, and cod-sync (or whatever
# sync protocol is in use) is responsible for orchestrating the actual
# commit → bundle → upload and download → unbundle → merge flows.
#
# The hub should never need to know about git repos or bundle chains;
# it just ferries opaque files to and from cloud storage.

import pathlib

import yaml

from . import protocol as CS


def commit_any_changes(repo_dir):
    """Commit uncommitted changes in a repo (if any)."""
    repo_dir = str(repo_dir)
    diff_q = CS.gitCmd(["-C", repo_dir, "diff", "--quiet"], raise_on_error=False)
    if diff_q.returncode != 0:
        CS.gitCmd(["-C", repo_dir, "add", "-A"])
        CS.gitCmd(["-C", repo_dir, "commit", "-m", "TODO: Better commit message"])


def sync_to_cloud(repo_dir, hub_upload_fn, cached_head_path=None):
    """Push local changes to cloud storage via the hub.

    hub_upload_fn: callable(path, data_bytes) that uploads a file
                   through the hub's cloud storage API.
    cached_head_path: path to a local YAML file caching the last-known
                      cloud head commit hash.

    TODO: This is an incomplete sketch. Needs:
    - Bundle creation (CodSync.push_to_remote logic)
    - ETag-based concurrency control on the chain head file
    - Integration with hub upload/download API
    - Error handling
    """
    repo_dir = pathlib.Path(repo_dir)

    commit_any_changes(repo_dir)

    rev_parse = CS.gitCmd(["-C", str(repo_dir), "rev-parse", "HEAD"])
    local_hash = rev_parse.stdout.strip()

    if cached_head_path:
        cached_head_path = pathlib.Path(cached_head_path)
        try:
            cached_head_str = cached_head_path.read_text()
            cached_head = yaml.safe_load(cached_head_str)
            cached_cloud_hash = cached_head.get("commit_hash")
        except FileNotFoundError:
            cached_cloud_hash = None
    else:
        cached_cloud_hash = None

    # TODO: create bundle from cached_cloud_hash..HEAD
    # TODO: upload bundle via hub_upload_fn
    # TODO: upload updated chain head via hub_upload_fn with etag if-match
    # TODO: update cached_head_path


def sync_from_cloud(repo_dir, hub_download_fn):
    """Pull changes from cloud storage via the hub.

    hub_download_fn: callable(path) -> bytes that downloads a file
                     through the hub's cloud storage API.

    TODO: This is an incomplete sketch. Needs:
    - Download chain head, walk prerequisite links
    - Download bundles
    - Unbundle and merge (with harmonic-merge for conflicts)
    """
    pass
