"""Hub-backed sync helpers for Shared File Vault."""

from __future__ import annotations

import json
import os
import pathlib
import tomllib
from dataclasses import dataclass
from typing import Callable, Optional

from cod_sync.protocol import (
    CasConflictError,
    GitCmdFailed,
    PeerSmallSeaRemote,
    SmallSeaRemote,
)
from small_sea_client.client import SmallSeaClient, SmallSeaError, SmallSeaSession

from shared_file_vault import vault

_CONFIG_PATH = pathlib.Path.home() / ".config" / "small-sea" / "vault.toml"
_HUB_APP_NAME = "SmallSeaCollectiveCore"
_CLI_CLIENT_NAME = "SharedFileVaultCLI"


class VaultSyncError(Exception):
    """Base class for Shared File Vault sync failures."""


class MissingConfigError(VaultSyncError):
    """Required Vault configuration is absent."""


class LoginRequiredError(VaultSyncError):
    """A valid cached team session is not available."""


class PushConflictError(VaultSyncError):
    """The user's cloud bucket has moved ahead of local state."""


class NothingToPushError(VaultSyncError):
    """A push was requested but there are no new commits."""


class PullConflictError(VaultSyncError):
    """A pull completed with unresolved merge conflicts."""

    def __init__(self, scope: str, paths: list[str]):
        self.scope = scope
        self.paths = paths
        path_text = ", ".join(paths) if paths else "unknown files"
        super().__init__(f"{scope} merge conflict: {path_text}")


class DirtyCheckoutError(VaultSyncError):
    """Merge rejected because the checkout has uncommitted changes.

    Publish or manually discard all changes (including untracked files)
    before integrating changes from teammates.
    """

    def __init__(self, paths: list[str]):
        self.paths = paths
        path_text = ", ".join(paths) if paths else "unknown files"
        super().__init__(f"Checkout is not clean: {path_text}")


class NoCheckoutError(VaultSyncError):
    """Merge rejected because no checkout is attached to this niche.

    Attach a checkout location before merging teammate changes.
    """

    def __init__(self, team_name: str, niche_name: str):
        self.team_name = team_name
        self.niche_name = niche_name
        super().__init__(
            f"Niche '{niche_name}' in team '{team_name}' has no local checkout. "
            "Attach a checkout before merging."
        )


class StaleCheckoutError(VaultSyncError):
    """Merge rejected because the registered checkout directory no longer exists.

    Remove the stale registration and re-attach at the correct path.
    """

    def __init__(self, team_name: str, niche_name: str, checkout_path: str):
        self.team_name = team_name
        self.niche_name = niche_name
        self.checkout_path = checkout_path
        super().__init__(
            f"Registered checkout '{checkout_path}' for niche '{niche_name}' no longer "
            "exists on disk. Remove the stale registration and re-attach."
        )


@dataclass
class FetchResult:
    member_id: str
    registry_sha: str | None
    niche_sha: str | None


@dataclass
class MergeResult:
    member_id: str
    registry_sha: str | None
    niche_sha: str | None


@dataclass
class PeerUpdateStatus:
    member_id: str
    parked_sha: str | None
    ready_to_merge: bool
    already_merged: bool
    registry_sha: str | None
    niche_sha: str | None
    last_fetched_sha: str | None
    last_merged_sha: str | None


@dataclass
class LoginResult:
    session_token: str
    session_info: dict
    auto_approved: bool


def config_path() -> pathlib.Path:
    """Return the Vault config path, honoring a test override env var."""
    override = os.environ.get("SMALL_SEA_VAULT_CONFIG")
    if override:
        return pathlib.Path(override)
    return _CONFIG_PATH


def load_config() -> dict:
    """Load Vault config if present, otherwise return an empty config dict."""
    path = config_path()
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        config = tomllib.load(f)
    config.setdefault("team_sessions", {})
    return config


def save_config(config: dict) -> None:
    """Persist Vault config as a small TOML file."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_toml(config))


def _dump_toml(config: dict) -> str:
    lines = []
    for key in ("vault_root", "participant_hex"):
        value = config.get(key)
        if value:
            lines.append(f"{key} = {json.dumps(str(value))}")

    hub_port = config.get("hub_port")
    if hub_port is not None:
        lines.append(f"hub_port = {int(hub_port)}")

    team_sessions = config.get("team_sessions") or {}
    for team_name in sorted(team_sessions):
        entry = team_sessions[team_name] or {}
        token = entry.get("session_token")
        if not token:
            continue
        if lines:
            lines.append("")
        lines.append(f"[team_sessions.{json.dumps(team_name)}]")
        lines.append(f"session_token = {json.dumps(str(token))}")

    return "\n".join(lines) + ("\n" if lines else "")


def store_session_token(team_name: str, session_token: str) -> None:
    """Persist a confirmed Hub session token for a team."""
    config = load_config()
    team_sessions = config.setdefault("team_sessions", {})
    team_sessions.setdefault(team_name, {})
    team_sessions[team_name]["session_token"] = session_token
    save_config(config)


def clear_session_token(team_name: str) -> None:
    """Remove any cached Hub session token for a team."""
    config = load_config()
    team_sessions = config.setdefault("team_sessions", {})
    if team_name in team_sessions:
        team_sessions.pop(team_name, None)
        save_config(config)


def registry_path_prefix(team_name: str) -> str:
    return f"vault/{team_name}/registry/"


def niche_path_prefix(team_name: str, niche_name: str) -> str:
    return f"vault/{team_name}/niches/{niche_name}/"


def login_team(
    team_name: str,
    participant_hex: str,
    hub_port: int = SmallSeaClient.DEFAULT_PORT,
    *,
    pin_reader: Optional[Callable[[str], str]] = None,
    _http_client=None,
) -> LoginResult:
    """Open or request a team-scoped Hub session and persist the token."""
    client = SmallSeaClient(port=hub_port, _http_client=_http_client)
    session, pending_id = client.start_session(
        participant_hex, _HUB_APP_NAME, team_name, _CLI_CLIENT_NAME
    )

    auto_approved = session is not None
    if session is None:
        if pin_reader is None:
            raise LoginRequiredError("A PIN is required to complete Vault login.")
        session = client.confirm_session(pending_id, pin_reader(pending_id).strip())

    info = session.session_info()
    if info.get("team_name") != team_name:
        raise VaultSyncError(
            f"Hub returned session for team {info.get('team_name')!r}, expected {team_name!r}"
        )
    if info.get("app_name") != _HUB_APP_NAME:
        raise VaultSyncError(
            f"Hub returned app {info.get('app_name')!r}, expected {_HUB_APP_NAME!r}"
        )

    store_session_token(team_name, session.token)
    return LoginResult(
        session_token=session.token,
        session_info=info,
        auto_approved=auto_approved,
    )


def get_team_session(
    team_name: str,
    hub_port: int = SmallSeaClient.DEFAULT_PORT,
    *,
    _http_client=None,
) -> SmallSeaSession:
    """Resume and validate a cached Hub session for a team."""
    config = load_config()
    token = (
        (config.get("team_sessions") or {})
        .get(team_name, {})
        .get("session_token")
    )
    if not token:
        raise LoginRequiredError(
            f"No cached Hub session for {team_name!r}. Run `shared-file-vault login {team_name}`."
        )

    client = SmallSeaClient(port=hub_port, _http_client=_http_client)
    session = SmallSeaSession(client, token)
    try:
        info = session.session_info()
    except SmallSeaError as exc:
        raise LoginRequiredError(
            f"Cached Hub session for {team_name!r} is no longer valid. "
            f"Run `shared-file-vault login {team_name}` again."
        ) from exc

    if info.get("team_name") != team_name:
        raise LoginRequiredError(
            f"Cached Hub session is for {info.get('team_name')!r}, expected {team_name!r}. "
            f"Run `shared-file-vault login {team_name}` again."
        )
    return session


def list_team_peers(
    team_name: str,
    hub_port: int = SmallSeaClient.DEFAULT_PORT,
    *,
    _http_client=None,
) -> list[dict]:
    """Return best-effort peer info for a cached team session."""
    session = get_team_session(team_name, hub_port=hub_port, _http_client=_http_client)
    return session.session_peers()


def _remote_kwargs(session: SmallSeaSession) -> dict:
    client = session._client
    return {
        "base_url": client._base_url,
        "client": client._http_client,
    }


def make_registry_remote(team_name: str, session: SmallSeaSession) -> SmallSeaRemote:
    return SmallSeaRemote(
        session.token,
        path_prefix=registry_path_prefix(team_name),
        **_remote_kwargs(session),
    )


def make_niche_remote(
    team_name: str, niche_name: str, session: SmallSeaSession
) -> SmallSeaRemote:
    return SmallSeaRemote(
        session.token,
        path_prefix=niche_path_prefix(team_name, niche_name),
        **_remote_kwargs(session),
    )


def make_peer_registry_remote(
    team_name: str, member_id: str, session: SmallSeaSession
) -> PeerSmallSeaRemote:
    return PeerSmallSeaRemote(
        session.token,
        member_id,
        path_prefix=registry_path_prefix(team_name),
        **_remote_kwargs(session),
    )


def make_peer_niche_remote(
    team_name: str,
    niche_name: str,
    member_id: str,
    session: SmallSeaSession,
) -> PeerSmallSeaRemote:
    return PeerSmallSeaRemote(
        session.token,
        member_id,
        path_prefix=niche_path_prefix(team_name, niche_name),
        **_remote_kwargs(session),
    )


def push_via_hub(
    vault_root: str,
    participant_hex: str,
    team_name: str,
    niche_name: str,
    *,
    hub_port: int = SmallSeaClient.DEFAULT_PORT,
    _http_client=None,
) -> None:
    """Push a niche and its registry through the Hub using a cached session."""
    session = get_team_session(team_name, hub_port=hub_port, _http_client=_http_client)
    session.ensure_cloud_ready()
    try:
        vault.push_niche(
            vault_root,
            participant_hex,
            team_name,
            niche_name,
            make_niche_remote(team_name, niche_name, session),
        )
    except CasConflictError as exc:
        raise PushConflictError(
            "Push conflict: cloud is ahead of local state. Pull from a teammate first."
        ) from exc
    except GitCmdFailed as exc:
        if "Refusing to create empty bundle" in exc.err:
            raise NothingToPushError(
                f"No new commits to push for niche {niche_name!r}."
            ) from exc
        raise

    try:
        vault.push_registry(
            vault_root,
            participant_hex,
            team_name,
            make_registry_remote(team_name, session),
        )
    except GitCmdFailed as exc:
        if "Refusing to create empty bundle" not in exc.err:
            raise


def pull_via_hub(
    vault_root: str,
    participant_hex: str,
    team_name: str,
    niche_name: str,
    from_member_id: str,
    *,
    hub_port: int = SmallSeaClient.DEFAULT_PORT,
    _http_client=None,
) -> None:
    """Pull a registry and niche from a peer through the Hub."""
    fetch_via_hub(
        vault_root,
        participant_hex,
        team_name,
        niche_name,
        from_member_id,
        hub_port=hub_port,
        _http_client=_http_client,
    )
    merge_via_hub(
        vault_root,
        participant_hex,
        team_name,
        niche_name,
        from_member_id,
        hub_port=hub_port,
        _http_client=_http_client,
    )


def fetch_via_hub(
    vault_root: str,
    participant_hex: str,
    team_name: str,
    niche_name: str,
    from_member_id: str,
    *,
    hub_port: int = SmallSeaClient.DEFAULT_PORT,
    _http_client=None,
) -> FetchResult:
    """Fetch a registry and niche from a peer through the Hub without merging."""
    session = get_team_session(team_name, hub_port=hub_port, _http_client=_http_client)
    registry_sha = vault.fetch_registry(
        vault_root,
        participant_hex,
        team_name,
        from_member_id,
        make_peer_registry_remote(team_name, from_member_id, session),
    )
    niche_sha = vault.fetch_niche(
        vault_root,
        participant_hex,
        team_name,
        niche_name,
        from_member_id,
        make_peer_niche_remote(team_name, niche_name, from_member_id, session),
    )
    return FetchResult(
        member_id=from_member_id,
        registry_sha=registry_sha,
        niche_sha=niche_sha,
    )


def merge_via_hub(
    vault_root: str,
    participant_hex: str,
    team_name: str,
    niche_name: str,
    from_member_id: str,
    *,
    hub_port: int = SmallSeaClient.DEFAULT_PORT,
    _http_client=None,
) -> MergeResult:
    """Merge already-fetched peer refs for a registry and niche.

    Preflights the niche checkout before touching the registry, so a
    dirty-checkout or no-checkout condition never leaves the registry
    merged while the niche merge is still pending.
    """
    _session = get_team_session(team_name, hub_port=hub_port, _http_client=_http_client)

    # Preflight: verify niche checkout exists and is clean before merging
    # anything. Without this, a failed niche merge would leave the registry
    # already integrated — a partially-merged state the user cannot easily undo.
    checkout = vault.get_checkout(vault_root, participant_hex, team_name, niche_name)
    if checkout is None:
        raise NoCheckoutError(team_name, niche_name)
    if not pathlib.Path(checkout).exists():
        raise StaleCheckoutError(team_name, niche_name, checkout)
    dirty_entries = vault.status(vault_root, participant_hex, team_name, niche_name, checkout)
    if dirty_entries:
        raise DirtyCheckoutError([e["path"] for e in dirty_entries])

    try:
        registry_sha = vault.merge_registry(
            vault_root,
            participant_hex,
            team_name,
            from_member_id,
        )
    except vault.MergeConflictError as exc:
        raise PullConflictError("registry", exc.paths) from exc

    try:
        niche_sha = vault.merge_niche(
            vault_root,
            participant_hex,
            team_name,
            niche_name,
            from_member_id,
        )
    except vault.MergeConflictError as exc:
        raise PullConflictError("niche", exc.paths) from exc
    except vault.DirtyCheckoutError as exc:
        raise DirtyCheckoutError(exc.paths) from exc
    except vault.NoCheckoutError as exc:
        raise NoCheckoutError(exc.team_name, exc.niche_name) from exc
    except vault.StaleCheckoutError as exc:
        raise StaleCheckoutError(exc.team_name, exc.niche_name, exc.checkout_path) from exc

    return MergeResult(
        member_id=from_member_id,
        registry_sha=registry_sha,
        niche_sha=niche_sha,
    )


def peer_update_status(
    vault_root: str,
    participant_hex: str,
    team_name: str,
    niche_name: str,
    member_id: str,
) -> PeerUpdateStatus:
    """Return combined registry+niche parked-update state for one peer."""
    registry_status = vault.peer_update_status(
        vault_root, participant_hex, team_name, "registry", None, member_id
    )
    niche_status = vault.peer_update_status(
        vault_root, participant_hex, team_name, "niche", niche_name, member_id
    )
    parked_sha = niche_status["parked_sha"] or registry_status["parked_sha"]
    ready_to_merge = (
        registry_status["ready_to_merge"] or niche_status["ready_to_merge"]
    )
    already_merged = bool(parked_sha) and not ready_to_merge
    return PeerUpdateStatus(
        member_id=member_id,
        parked_sha=parked_sha,
        ready_to_merge=ready_to_merge,
        already_merged=already_merged,
        registry_sha=registry_status["parked_sha"],
        niche_sha=niche_status["parked_sha"],
        last_fetched_sha=niche_status["last_fetched_sha"] or registry_status["last_fetched_sha"],
        last_merged_sha=niche_status["last_merged_sha"] or registry_status["last_merged_sha"],
    )


def require_value(value, name: str) -> str:
    """Return a required config/CLI value or raise a helpful error."""
    if value:
        return value
    raise MissingConfigError(
        f"{name} is required. Set it in {config_path()} or pass it explicitly."
    )
