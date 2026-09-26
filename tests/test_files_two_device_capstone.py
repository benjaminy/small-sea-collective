"""Capstone micro test for #235: one participant, two devices, Files.

Two blank installations, each with its own in-process Hub (a separately
loaded server module, so each has its own FastAPI app and backend) and its own
Manager and Files roots.
One MinIO service stands in for the participant's cloud provider.
The only things that cross between the devices are the out-of-band artifacts a
person would carry: the identity-join request and welcome, and the linked-team
join request and bootstrap response.

Every provider call is attributed to the Hub whose backend made it; see
`_ProviderSpy`.

Not yet covered: Files publications and commits are unsigned (#266), so a
fetched head proves what the store held, not which device wrote it.
"""

import importlib.util
import inspect
import pathlib
import sqlite3
import sys
from urllib.parse import unquote, urlsplit

import boto3
import pytest
from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from ssc_files import files, sync

TEAM = "Garden"
NICHE = "plans"
FILES_APP = sync.HUB_APP_NAME


def _load_hub_app(tag):
    """Load a fresh copy of the Hub server module, giving an independent app."""
    import small_sea_hub.server as template

    name = f"_capstone_hub_server_{tag}"
    spec = importlib.util.spec_from_file_location(name, template.__file__)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.app


class _Device:
    def __init__(self, name, root, monkeypatch):
        self.name = name
        self.root = root
        self.files_root = str(root / "files")
        self.config = root / "ssc-files.toml"
        self.monkeypatch = monkeypatch
        self.backend = SmallSea.SmallSeaBackend(
            root_dir=str(root), auto_approve_sessions=True
        )
        app = _load_hub_app(name)
        app.state.backend = self.backend
        self.http = TestClient(app)

    def use_files(self):
        """Point Files' per-user config at this device's file."""
        self.monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(self.config))


class _ProviderSpy:
    """Record every S3 call and the Hub backend that made it.

    Wraps `boto3.client` and walks the stack at client-creation time to find
    the `SmallSeaBackend` responsible. A call made outside any Hub is recorded
    with owner None, which fails `assert_only`.
    """

    def __init__(self, monkeypatch, devices):
        self.devices = devices
        self.calls = []
        real_client = boto3.client

        def spying_client(*args, **kwargs):
            owner = None
            for frame_info in inspect.stack():
                candidate = frame_info.frame.f_locals.get("self")
                if isinstance(candidate, SmallSea.SmallSeaBackend):
                    owner = candidate
                    break
            client = real_client(*args, **kwargs)
            client.meta.events.register(
                "before-call.s3.*",
                lambda model, params, **_: self.calls.append(
                    (self._device_name(owner), model.name, params.get("Bucket"))
                ),
            )
            return client

        monkeypatch.setattr(boto3, "client", spying_client)

    def _device_name(self, backend):
        for device in self.devices:
            if device.backend is backend:
                return device.name
        return None

    def mark(self):
        return len(self.calls)

    def assert_only(self, since, device_name):
        window = self.calls[since:]
        assert window, f"expected provider traffic from {device_name}'s Hub"
        wrong = [c for c in window if c[0] != device_name]
        assert not wrong, f"provider calls not made by {device_name}'s Hub: {wrong}"


def _git_head(git_dir):
    import subprocess

    return subprocess.run(
        ["git", "--git-dir", str(git_dir), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class _ManagerDatabaseGuard:
    """Track Manager-owned database opens made from Files code."""

    def __init__(self, monkeypatch, devices, participant_hex):
        import small_sea_manager.provisioning as Provisioning
        from small_sea_note_to_self.db import (
            device_local_db_path,
            note_to_self_sync_db_path,
        )

        self.opens = []
        self.protected_dirs = []
        for device in devices:
            self.protected_dirs.extend(
                (
                    note_to_self_sync_db_path(device.root, participant_hex).parent.parent,
                    device_local_db_path(device.root, participant_hex).parent.parent,
                    Provisioning._team_sync_dir(device.root, participant_hex, TEAM),
                )
            )
        self.protected_dirs = [path.resolve() for path in self.protected_dirs]
        real_connect = sqlite3.connect

        def guarded_connect(database, *args, **kwargs):
            path = _sqlite_database_path(database)
            if path is not None:
                stack = inspect.stack()
                has_files_frame = any(
                    (frame.frame.f_globals.get("__name__") or "").startswith(
                        "ssc_files"
                    )
                    for frame in stack
                )
                has_hub_frame = any(
                    (frame.frame.f_globals.get("__name__") or "").startswith(
                        "small_sea_hub"
                    )
                    for frame in stack
                )
                if has_files_frame and not has_hub_frame:
                    self.opens.append(path)
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", guarded_connect)

    def violations(self):
        return [
            opened
            for opened in self.opens
            if any(opened == root or root in opened.parents for root in self.protected_dirs)
        ]

    def assert_clean(self):
        violations = self.violations()
        assert not violations, f"Files opened Manager-owned SQLite databases: {violations}"


def _sqlite_database_path(database):
    """Convert sqlite3.connect's path argument, including file: URIs, to Path."""
    if isinstance(database, bytes):
        database = database.decode()
    if isinstance(database, pathlib.Path):
        return database.resolve()
    if not isinstance(database, str) or database == ":memory:":
        return None
    if database.startswith("file:"):
        parsed = urlsplit(database)
        if parsed.path in ("", "/:memory:"):
            return None
        database = unquote(parsed.path)
    return pathlib.Path(database).resolve()


def test_files_database_guard_detects_manager_database_open(tmp_path, monkeypatch):
    participant_hex = "ab" * 16
    device = _Device("negative-check", tmp_path / "device", monkeypatch)
    guard = _ManagerDatabaseGuard(monkeypatch, [device], participant_hex)
    import small_sea_manager.provisioning as Provisioning

    database_path = Provisioning._team_sync_dir(device.root, participant_hex, TEAM) / "core.db"
    database_path.parent.mkdir(parents=True)
    namespace = {
        "__name__": "ssc_files.guard_self_check",
        "sqlite3": sqlite3,
        "database_path": database_path,
    }
    code = compile(
        "def open_manager_database():\n    sqlite3.connect(database_path)\n",
        "ssc_files_guard_self_check.py",
        "exec",
    )
    exec(code, namespace)
    namespace["open_manager_database"]()
    with pytest.raises(AssertionError, match="Manager-owned SQLite databases"):
        guard.assert_clean()


def test_one_participant_two_devices_share_files(tmp_path, monkeypatch, minio_server_gen):
    minio = minio_server_gen()
    a = _Device("A", tmp_path / "device-a", monkeypatch)
    b = _Device("B", tmp_path / "device-b", monkeypatch)
    spy = _ProviderSpy(monkeypatch, [a, b])

    # 1. Participant and MinIO-backed cloud account on A, through Manager.
    import small_sea_manager.provisioning as Provisioning

    alice_hex = Provisioning.create_new_participant(a.root, "Alice")
    manager_a = TeamManager(a.root, alice_hex, _http_client=a.http)
    manager_a.add_cloud_storage(
        protocol="s3", url=minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )

    # 2-4. Team, Files registration/activation, Core and Files berth routes.
    mark = spy.mark()
    manager_a.create_team(TEAM)
    manager_a.register_app_for_participant(FILES_APP)
    manager_a.activate_app_for_team(TEAM, FILES_APP)
    assert manager_a.reconcile_team_route(TEAM)["route"] == "ready"
    assert manager_a.reconcile_team_route(TEAM, app_name=FILES_APP)["route"] == "ready"
    assert manager_a.push_team(TEAM) == "published"
    manager_a.push_note_to_self()
    spy.assert_only(mark, "A")

    db_guard = _ManagerDatabaseGuard(monkeypatch, [a, b], alice_hex)

    # 5. Files on A: login, niche, checkout, publish, push.
    a.use_files()
    mark = spy.mark()
    login_a = sync.login_team(a.files_root, TEAM, alice_hex, _http_client=a.http)
    ctx_a = files.materialization_context_from_session_info(login_a.session_info)
    files.create_niche(a.files_root, alice_hex, ctx_a, NICHE)
    checkout_a = a.root / "checkout"
    files.add_checkout(a.files_root, alice_hex, ctx_a, NICHE, str(checkout_a))
    original = b"tomatoes by the fence\n"
    (checkout_a / "beds.txt").write_bytes(original)
    files.publish(a.files_root, alice_hex, ctx_a, NICHE, str(checkout_a), message="A1")
    sync.push_via_hub(a.files_root, alice_hex, TEAM, NICHE, _http_client=a.http)
    spy.assert_only(mark, "A")

    # 6. Identity join: B's request is couriered to A, A's welcome back to B.
    join_request = create_identity_join_request(b.root)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    mark = spy.mark()
    bootstrap_existing_identity(b.root, welcome["welcome_bundle"], _http_client=b.http)
    spy.assert_only(mark, "B")

    # 7. B enrolls its own credentials for the synced account.
    manager_b = TeamManager(b.root, alice_hex, _http_client=b.http)
    (account,) = manager_b.list_cloud_storage()
    manager_b.connect_cloud_storage_credentials(
        account["id"], access_key=minio["access_key"], secret_key=minio["secret_key"],
    )

    # 8. Refresh NoteToSelf on B; the team is known but not materialized.
    mark = spy.mark()
    manager_b.refresh_note_to_self()
    spy.assert_only(mark, "B")
    known = next(t for t in manager_b.list_known_teams() if t["name"] == TEAM)
    assert known["joined_locally"] is False

    # 9. Linked-device bootstrap and sender-key redistribution.
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])
    manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])
    assert manager_b.get_team(TEAM)["joined_locally"] is True
    redistribution = Provisioning.redistribute_sender_key(b.root, alice_hex, TEAM)
    for artifact in redistribution["artifacts"]:
        Provisioning.receive_sender_key_distribution(
            a.root, alice_hex, TEAM, artifact["distribution_payload"],
        )

    # 10. Files on B derives its context from B's Hub session.
    b.use_files()
    mark = spy.mark()
    login_b = sync.login_team(b.files_root, TEAM, alice_hex, _http_client=b.http)
    ctx_b = files.materialization_context_from_session_info(login_b.session_info)
    assert ctx_b.team_id == ctx_a.team_id

    # 11. Fetch B's own registry through the Hub, discover the niche, then fetch it.
    fetched_registry = sync.fetch_self_via_hub(
        b.files_root, alice_hex, TEAM, _http_client=b.http
    )
    assert fetched_registry.registry_sha
    files.merge_self_registry(b.files_root, alice_hex, ctx_b)
    discovered = [n["name"] for n in files.list_niches(b.files_root, alice_hex, ctx_b)]
    assert "plans" in discovered
    niche = discovered[0]
    assert niche == NICHE
    fetched = sync.fetch_self_via_hub(
        b.files_root, alice_hex, TEAM, niche, _http_client=b.http
    )
    assert fetched.niche_sha == _git_head(files._niche_git_dir(a.files_root, ctx_a, niche))
    checkout_b = b.root / "checkout"
    files.add_checkout(b.files_root, alice_hex, ctx_b, niche, str(checkout_b))
    sync.merge_self(b.files_root, alice_hex, TEAM, niche)
    assert (checkout_b / "beds.txt").read_bytes() == original

    # 12. B changes the file and pushes through B's Hub.
    from_b = b"tomatoes by the fence\nbeans on the trellis\n"
    (checkout_b / "beds.txt").write_bytes(from_b)
    files.publish(b.files_root, alice_hex, ctx_b, niche, str(checkout_b), message="B1")
    sync.push_via_hub(b.files_root, alice_hex, TEAM, niche, _http_client=b.http)
    spy.assert_only(mark, "B")

    # 13. A fetches and integrates B's change through A's Hub.
    a.use_files()
    mark = spy.mark()
    sync.fetch_self_via_hub(a.files_root, alice_hex, TEAM, NICHE, _http_client=a.http)
    sync.merge_self(a.files_root, alice_hex, TEAM, NICHE)
    assert (checkout_a / "beds.txt").read_bytes() == from_b

    # 14. A publishes again on the converged history, with no conflict.
    (checkout_a / "beds.txt").write_bytes(from_b + b"squash in the corner\n")
    files.publish(a.files_root, alice_hex, ctx_a, NICHE, str(checkout_a), message="A2")
    sync.push_via_hub(a.files_root, alice_hex, TEAM, NICHE, _http_client=a.http)
    spy.assert_only(mark, "A")
    db_guard.assert_clean()
    assert all(owner in ("A", "B") for owner, _op, _bucket in spy.calls)
