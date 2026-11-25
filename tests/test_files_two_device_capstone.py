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

Every Files commit is signed with the session's real workhorse key, whose
delegation comes from the identity join and linked-device bootstrap.
Each device's Hub checks the histories it fetches through `/session/verify`,
and the test asks the Hub to verify each device's final Files history.
The one test shortcut is that the key is exported to a file for `ssh-keygen`,
because the Hub's signing program is not installed here.

`test_signed_capstone_refuses_bad_history` covers histories A must refuse:
unsigned, wrong-berth key, missing authority, ambiguous authority.
Not yet covered: a changed authority view (needs the action guard, #286).
"""
from dataclasses import replace
import functools
import importlib.util
import inspect
import os
import pathlib
import shutil
import sqlite3
import sys
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import anyio
import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from cod_sync.git import signing_git_env
from cod_sync.repo import Repo
import small_sea_hub.backend as SmallSea
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_client import client as hub_client
from ssc_files import files, sync

TEAM = "Garden"
NICHE = "plans"
FILES_APP = sync.HUB_APP_NAME


def _load_hub_server(tag):
    """Load a fresh copy of the Hub server module, giving an independent app."""
    import small_sea_hub.server as template

    name = f"_capstone_hub_server_{tag}"
    spec = importlib.util.spec_from_file_location(name, template.__file__)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Device:
    def __init__(self, name, root, monkeypatch):
        self.name = name
        self.root = root
        # The Hub refuses git dirs inside the Small Sea root, so Files data lives beside it.
        self.files_root = str(root.parent / f"{root.name}-files")
        self.config = root / "ssc-files.toml"
        self.monkeypatch = monkeypatch
        self.backend = SmallSea.SmallSeaBackend(
            root_dir=str(root), auto_approve_sessions=True
        )
        self.hub = _load_hub_server(name)
        self.hub.app.state.backend = self.backend
        self.http = TestClient(self.hub.app)

    def watcher_pass(self):
        """Run one pass of this Hub's runtime watch, as its background loop would.

        The TestClient does not run the Hub's lifespan, so the first call sets up
        the watcher state and registers the sessions opened so far, as a Hub
        restart does.
        Sessions opened later register themselves through the Hub's routes.
        """
        state = self.hub.app.state
        if not hasattr(state, "watched_sessions"):
            state.watched_sessions = {}
            state.watched_peers = {}
            state.peer_counts = {}
            state.self_signal_counts = {}
            state.peer_signal_events = {}
            state.ntfy_listener_berths = set()
            state.logger = self.backend.logger
            for token_hex in self.backend.all_session_tokens():
                self.hub._register_session_peers(token_hex)
        anyio.run(self.hub._watcher_pass, self.hub.app)

    async def signer(self, team_name, hub_port=11437, *, _http_client=None, key_token=None):
        """A CommitSigner using this session's real workhorse key and the Hub's verifier.

        `key_token` names another session whose workhorse key signs instead,
        for tests that sign with a key scoped to a different berth.
        """
        async with sync.hub_http(hub_port, _http_client) as http:
            session = sync.get_team_session(team_name, http)
            key_owner = session if key_token is None else hub_client.SmallSeaSession(http, key_token)
            info = await session.session_info()
            authority_view = await session.authority_view()
            public_key = await key_owner.signing_public_key()
        seed = self.backend.signing_key(key_owner.token)
        key_path = self.root.parent / f"{self.root.name}-key-{key_owner.token[:8]}"
        key_path.write_bytes(Ed25519PrivateKey.from_private_bytes(seed).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption()))
        key_path.chmod(0o600)
        env = signing_git_env(
            os.environ, program=shutil.which("ssh-keygen"),
            public_key=public_key, signing_key=str(key_path),
            extra_env={"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
                       "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"},
        )
        return files.CommitSigner(info["team_id"], info["berth_id"], authority_view, env,
                                  sync.hub_verifier(session.token, hub_port, _http_client=_http_client),
                                  session.token)

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
                # Files reaches each device's in-process Hub on its own
                # thread, so Hub frames, including the separately loaded
                # server modules, can sit above Files frames on one stack.
                has_hub_frame = any(
                    (frame.frame.f_globals.get("__name__") or "").startswith(
                        ("small_sea_hub", "_capstone_hub_server_")
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


def _b_sender_keys_held_by_a(env):
    import small_sea_manager.provisioning as Provisioning

    _private, b_public = Provisioning.get_current_team_device_key(env.b.root, env.alice_hex, TEAM)
    b_key_id = Provisioning.key_id_from_public(b_public)
    db = Provisioning.device_local_db_path(env.a.root, env.alice_hex)
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(
            "SELECT chain_id FROM peer_sender_key WHERE sender_device_key_id = ?",
            (b_key_id,),
        ).fetchall()


def _two_devices_until_b_has_files(tmp_path, monkeypatch, minio_server_gen, *, b_watches_before_edit=True):
    """Steps 1-11: both devices set up, B holds A's Files niche in a checkout.

    With `b_watches_before_edit`, B's Hub runs a watcher pass at the end, which
    prepares and uploads B's sender-key distribution for A.
    """
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
    assert anyio.run(manager_a.reconcile_team_route, TEAM)["route"] == "ready"
    assert anyio.run(functools.partial(manager_a.reconcile_team_route, TEAM, app_name=FILES_APP))["route"] == "ready"
    assert anyio.run(manager_a.push_team, TEAM) == "published"
    anyio.run(manager_a.push_note_to_self)
    spy.assert_only(mark, "A")

    db_guard = _ManagerDatabaseGuard(monkeypatch, [a, b], alice_hex)

    monkeypatch.setattr(sync, "commit_signer", a.signer)

    # 5. Files on A: login, niche, checkout, publish, push.
    a.use_files()
    mark = spy.mark()
    login_a = anyio.run(functools.partial(sync.login_team, a.files_root, TEAM, alice_hex, _http_client=a.http))
    ctx_a = files.materialization_context_from_session_info(login_a.session_info)
    files.create_niche(a.files_root, alice_hex, ctx_a, NICHE, signer=anyio.run(functools.partial(a.signer, TEAM, _http_client=a.http)))
    checkout_a = a.root / "checkout"
    files.add_checkout(a.files_root, alice_hex, ctx_a, NICHE, str(checkout_a))
    original = b"tomatoes by the fence\n"
    (checkout_a / "beds.txt").write_bytes(original)
    files.publish(a.files_root, alice_hex, ctx_a, NICHE, str(checkout_a), message="A1", signer=anyio.run(functools.partial(a.signer, TEAM, _http_client=a.http)))
    anyio.run(functools.partial(sync.push_via_hub, a.files_root, alice_hex, TEAM, NICHE, _http_client=a.http))
    spy.assert_only(mark, "A")

    # 6. Identity join: B's request is couriered to A, A's welcome back to B.
    join_request = create_identity_join_request(b.root)
    welcome = anyio.run(manager_a.authorize_identity_join, join_request["join_request_artifact"])
    mark = spy.mark()
    anyio.run(functools.partial(bootstrap_existing_identity, b.root, welcome["welcome_bundle"], _http_client=b.http))
    spy.assert_only(mark, "B")

    # 7. B enrolls its own credentials for the synced account.
    manager_b = TeamManager(b.root, alice_hex, _http_client=b.http)
    (account,) = manager_b.list_cloud_storage()
    manager_b.connect_cloud_storage_credentials(
        account["id"], access_key=minio["access_key"], secret_key=minio["secret_key"],
    )

    # 8. Refresh NoteToSelf on B; the team is known but not materialized.
    mark = spy.mark()
    anyio.run(manager_b.refresh_note_to_self)
    spy.assert_only(mark, "B")
    known = next(t for t in manager_b.list_known_teams() if t["name"] == TEAM)
    assert known["joined_locally"] is False

    # 9. Linked-device bootstrap.
    # B's sender key reaches A later, through the Hubs' runtime watch.
    prepared = manager_b.prepare_linked_device_team_join(TEAM)
    created = manager_a.create_linked_device_bootstrap(TEAM, prepared["join_request_bundle"])
    manager_b.finalize_linked_device_bootstrap(TEAM, created["bootstrap_bundle"])
    assert manager_b.get_team(TEAM)["joined_locally"] is True

    # 10. Files on B derives its context from B's Hub session.
    b.use_files()
    mark = spy.mark()
    login_b = anyio.run(functools.partial(sync.login_team, b.files_root, TEAM, alice_hex, _http_client=b.http))
    ctx_b = files.materialization_context_from_session_info(login_b.session_info)
    assert ctx_b.team_id == ctx_a.team_id
    monkeypatch.setattr(sync, "commit_signer", b.signer)

    # 11. Fetch B's own registry through the Hub, discover the niche, then fetch it.
    fetched_registry = anyio.run(functools.partial(sync.fetch_self_via_hub, b.files_root, alice_hex, TEAM, _http_client=b.http))
    assert fetched_registry.registry_sha
    anyio.run(functools.partial(files.merge_self_registry, b.files_root, alice_hex, ctx_b, signer=anyio.run(functools.partial(b.signer, TEAM, _http_client=b.http))))
    discovered = [n["name"] for n in files.list_niches(b.files_root, alice_hex, ctx_b)]
    assert "plans" in discovered
    niche = discovered[0]
    assert niche == NICHE
    fetched = anyio.run(functools.partial(sync.fetch_self_via_hub, b.files_root, alice_hex, TEAM, niche, _http_client=b.http))
    assert fetched.niche_sha == _git_head(files._niche_git_dir(a.files_root, ctx_a, niche))
    checkout_b = b.root / "checkout"
    files.add_checkout(b.files_root, alice_hex, ctx_b, niche, str(checkout_b))
    anyio.run(functools.partial(sync.merge_self, b.files_root, alice_hex, TEAM, niche, _http_client=b.http))
    assert (checkout_b / "beds.txt").read_bytes() == original

    if b_watches_before_edit:
        mark = spy.mark()
        b.watcher_pass()
        spy.assert_only(mark, "B")

    return SimpleNamespace(
        a=a, b=b, spy=spy, db_guard=db_guard, alice_hex=alice_hex,
        manager_a=manager_a, manager_b=manager_b, ctx_a=ctx_a, ctx_b=ctx_b,
        checkout_a=checkout_a, checkout_b=checkout_b, original=original, niche=niche,
    )


@pytest.mark.parametrize("b_watches", [
    "before_edit",
    pytest.param("after_push", marks=pytest.mark.xfail(
        raises=sync.SelfFetchPartialError, strict=True,
        reason="#280: B prepares its distribution after the push, at a later "
               "sender-key iteration, so A cannot decrypt B's niche links.")),
])
def test_signed_files_two_device_flow(tmp_path, monkeypatch, minio_server_gen, b_watches):
    """B's Hub first runs its watcher either before B edits or right after B pushes.

    Either way A's Hub runs its watcher after both, and must receive B's key.
    """
    env = _two_devices_until_b_has_files(
        tmp_path, monkeypatch, minio_server_gen,
        b_watches_before_edit=(b_watches == "before_edit"))
    a, b, spy, db_guard = env.a, env.b, env.spy, env.db_guard
    alice_hex, manager_a, manager_b = env.alice_hex, env.manager_a, env.manager_b
    ctx_a, ctx_b, checkout_a, checkout_b, niche = (
        env.ctx_a, env.ctx_b, env.checkout_a, env.checkout_b, env.niche)

    # 12. B changes the file and pushes through B's Hub.
    mark = spy.mark()
    from_b = b"tomatoes by the fence\nbeans on the trellis\n"
    (checkout_b / "beds.txt").write_bytes(from_b)
    files.publish(b.files_root, alice_hex, ctx_b, niche, str(checkout_b), message="B1", signer=anyio.run(functools.partial(b.signer, TEAM, _http_client=b.http)))
    anyio.run(functools.partial(sync.push_via_hub, b.files_root, alice_hex, TEAM, niche, _http_client=b.http))
    if b_watches == "after_push":
        b.watcher_pass()
    spy.assert_only(mark, "B")

    # A's Hub picks B's sender key out of Alice's storage.
    assert _b_sender_keys_held_by_a(env) == []
    mark = spy.mark()
    a.watcher_pass()
    spy.assert_only(mark, "A")
    assert len(_b_sender_keys_held_by_a(env)) == 1

    # 13. A learns B's signing delegation, then fetches and integrates B's change.
    # B's key was delegated in B's Core chain when B first signed.
    # A's Hub can only accept B's commits once A has integrated that chain.
    mark = spy.mark()
    anyio.run(manager_b.push_team, TEAM)
    spy.assert_only(mark, "B")
    a.use_files()
    monkeypatch.setattr(sync, "commit_signer", a.signer)
    mark = spy.mark()
    (participant,) = manager_a.list_teammates(TEAM)
    anyio.run(manager_a.fetch_teammate_core, TEAM, participant["id"])
    manager_a.integrate_core_sources(TEAM)
    anyio.run(functools.partial(sync.fetch_self_via_hub, a.files_root, alice_hex, TEAM, NICHE, _http_client=a.http))
    anyio.run(functools.partial(sync.merge_self, a.files_root, alice_hex, TEAM, NICHE, _http_client=a.http))
    assert (checkout_a / "beds.txt").read_bytes() == from_b

    # 14. A publishes again on the converged history, with no conflict.
    (checkout_a / "beds.txt").write_bytes(from_b + b"squash in the corner\n")
    files.publish(a.files_root, alice_hex, ctx_a, NICHE, str(checkout_a), message="A2", signer=anyio.run(functools.partial(a.signer, TEAM, _http_client=a.http)))
    anyio.run(functools.partial(sync.push_via_hub, a.files_root, alice_hex, TEAM, NICHE, _http_client=a.http))
    spy.assert_only(mark, "A")
    db_guard.assert_clean()

    # 15. Each device's Hub accepts every commit in that device's final Files history.
    for device, context in ((a, ctx_a), (b, ctx_b)):
        device.use_files()
        git_dir = files._niche_git_dir(device.files_root, context, NICHE)
        verify = sync.hub_verifier(sync.cached_session_token(TEAM), _http_client=device.http)
        verdicts = anyio.run(verify, git_dir, _git_head(git_dir))["commits"]
        assert verdicts, f"no commits verified on {device.name}"
        assert {v["result"] for v in verdicts} == {"authorized"}, verdicts
    assert all(owner in ("A", "B") for owner, _op, _bucket in spy.calls)


def _refusals(error):
    """The Hub's refused-commit rows from a fetch failure, unwrapping a partial fetch."""
    if isinstance(error, sync.SelfFetchPartialError):
        error = error.niche_error
    assert isinstance(error, files.UnauthorizedHistoryError), repr(error)
    return error.refusals


@pytest.mark.parametrize("case, expected", [
    ("unsigned", "bad_signature"),
    ("wrong_berth", "wrong_scope"),
    ("missing_authority", "missing_authority"),
    ("ambiguous_authority", "ambiguous_authority"),
])
def test_signed_capstone_refuses_bad_history(
    tmp_path, monkeypatch, minio_server_gen, case, expected
):
    env = _two_devices_until_b_has_files(tmp_path, monkeypatch, minio_server_gen)
    a, b, alice_hex, niche = env.a, env.b, env.alice_hex, env.niche
    import small_sea_manager.provisioning as Provisioning
    a.watcher_pass()

    # B commits something A must refuse.
    # B's own Hub would refuse to publish it, so B's verifier is replaced by one that accepts everything.
    b.use_files()
    key_token = None
    if case == "wrong_berth":
        key_token = b.backend.open_session(
            "Alice", "SmallSeaCollectiveCore", TEAM, "Smoke Tests").hex()
    signer = anyio.run(functools.partial(b.signer, TEAM, _http_client=b.http, key_token=key_token))
    if case == "unsigned":
        signer = replace(signer, env={
            k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")
        } | {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"})
    async def _accept_everything(_git_dir, sha):
        return {
            "commits": [{"commit": sha, "result": "authorized", "reason": "test"}],
            "view_identifier": b"",
        }

    rogue = replace(signer, verify=_accept_everything)

    async def _rogue_signer(*_a, **_k):
        return rogue

    monkeypatch.setattr(sync, "commit_signer", _rogue_signer)
    (env.checkout_b / "beds.txt").write_bytes(b"bad change\n")
    files.publish(b.files_root, alice_hex, env.ctx_b, niche, str(env.checkout_b),
                  message="B-bad", signer=rogue)
    anyio.run(functools.partial(sync.push_via_hub, b.files_root, alice_hex, TEAM, niche, _http_client=b.http))
    bad_head = _git_head(files._niche_git_dir(b.files_root, env.ctx_b, niche))

    # A learns B's delegations, except in the missing-authority case.
    if case != "missing_authority":
        anyio.run(env.manager_b.push_team, TEAM)
        (participant,) = env.manager_a.list_teammates(TEAM)
        anyio.run(env.manager_a.fetch_teammate_core, TEAM, participant["id"])
        env.manager_a.integrate_core_sources(TEAM)
    if case == "ambiguous_authority":
        # Two records that grant and then restrict the same standing cannot be ordered.
        _team_id, teammate_id = Provisioning._team_row(a.root, alice_hex, TEAM)
        async def _berth_id():
            async with sync.hub_http(_http_client=b.http) as http:
                return (await sync.get_team_session(TEAM, http).session_info())["berth_id"]

        berth_id = anyio.run(_berth_id)
        for mode in ("automatic", "proposal-only"):
            Provisioning.set_teammate_integration_mode(
                a.root, alice_hex, TEAM, teammate_id, berth_id, mode)

    a.use_files()
    monkeypatch.setattr(sync, "commit_signer", a.signer)
    git_dir = files._niche_git_dir(a.files_root, env.ctx_a, niche)
    refs_before = Repo(git_dir).list_refs("refs/")
    head_before = _git_head(git_dir)
    content_before = (env.checkout_a / "beds.txt").read_bytes()

    with pytest.raises((files.UnauthorizedHistoryError, sync.SelfFetchPartialError)) as caught:
        anyio.run(functools.partial(sync.fetch_self_via_hub, a.files_root, alice_hex, TEAM, niche, _http_client=a.http))
    refusals = _refusals(caught.value)
    if case != "ambiguous_authority":
        # Every A and B commit is ambiguous in that case, so the registry is refused before the niche.
        assert bad_head in {row["commit"] for row in refusals}, refusals
    assert expected in {row["result"] for row in refusals}, refusals

    assert Repo(git_dir).list_refs("refs/") == refs_before
    assert bad_head not in Repo(git_dir).list_refs("refs/").values()
    assert _git_head(git_dir) == head_before
    assert (env.checkout_a / "beds.txt").read_bytes() == content_before
