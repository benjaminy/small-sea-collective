# Top Matter

import pathlib
import subprocess
from dataclasses import dataclass
from typing import Optional

from small_sea_client.client import SmallSeaClient
from small_sea_manager import provisioning


@dataclass
class PushResult:
    ok: bool
    reason: Optional[str] = None  # e.g. "behind" on CAS conflict


@dataclass
class PullResult:
    has_conflicts: bool


class TeamManager:
    """Business logic for team management operations.

    Reads team/member/invitation data directly from the local SQLite DB.
    Hub sessions (via SmallSeaClient) are used only for cloud sync operations.
    """

    def __init__(self, root_dir, participant_hex, hub_port=11437, _http_client=None):
        self.root_dir = pathlib.Path(root_dir)
        self.participant_hex = participant_hex
        provisioning.migrate_participant_team_dbs(self.root_dir, self.participant_hex)
        self.client = SmallSeaClient(port=hub_port, _http_client=_http_client)
        # Confirmed sessions, keyed by (app, team, mode).
        self._sessions: dict[tuple[str, str, str], "SmallSeaSession"] = {}
        # Pending PIN requests awaiting confirmation, keyed by (app, team, mode).
        self._pending: dict[tuple[str, str, str], str] = {}

    # ------------------------------------------------------------------ #
    # Session state management
    # ------------------------------------------------------------------ #

    @staticmethod
    def _session_key(app: str, team: str, mode: str = "encrypted") -> tuple[str, str, str]:
        return (app, team, mode)

    def set_session(self, app: str, team: str, token: str, mode: str = "encrypted") -> None:
        """Store a confirmed session token for (app, team, mode)."""
        from small_sea_client.client import SmallSeaSession
        key = self._session_key(app, team, mode)
        self._sessions[key] = SmallSeaSession(self.client, token)
        self._pending.pop(key, None)

    def clear_session(self, app: str, team: str, mode: str = "encrypted") -> None:
        """Remove the confirmed session for (app, team, mode)."""
        self._sessions.pop(self._session_key(app, team, mode), None)

    def set_pending(
        self, app: str, team: str, pending_id: str, mode: str = "encrypted"
    ) -> None:
        """Record a pending PIN request for (app, team, mode)."""
        self._pending[self._session_key(app, team, mode)] = pending_id

    def clear_pending(self, app: str, team: str, mode: str = "encrypted") -> None:
        self._pending.pop(self._session_key(app, team, mode), None)

    def session_state(self, app: str, team: str, mode: str = "encrypted") -> str:
        """Return 'active', 'pending', or 'none' for the given (app, team, mode)."""
        key = self._session_key(app, team, mode)
        if key in self._sessions:
            return "active"
        if key in self._pending:
            return "pending"
        return "none"

    def get_pending_id(self, app: str, team: str, mode: str = "encrypted") -> str | None:
        return self._pending.get(self._session_key(app, team, mode))

    def active_sessions(self) -> list[dict]:
        """Return a list of {app, team, mode} dicts for all confirmed sessions."""
        return [
            {"app": app, "team": team, "mode": mode}
            for (app, team, mode) in self._sessions
        ]

    def _get_or_open_session(
        self, app: str, team: str, mode: str = "encrypted"
    ) -> "SmallSeaSession":
        """Return a confirmed session for (app, team, mode).

        Uses the cached session if one has been established (e.g. via PIN
        flow). Otherwise opens a new session via open_session, which requires
        the Hub to be in auto-approve mode.
        """
        key = self._session_key(app, team, mode)
        if key in self._sessions:
            return self._sessions[key]
        return self.client.open_session(
            self.participant_hex, app, team, "TeamManager", mode=mode
        )

    def get_nickname(self):
        """Return the participant's first nickname, or a short hex fallback."""
        return provisioning.get_nickname(self.root_dir, self.participant_hex)

    def _cloud(self):
        """Return the participant's primary cloud storage config dict."""
        return provisioning.get_cloud_storage(self.root_dir, self.participant_hex)

    def list_cloud_storage(self):
        """Return all cloud storage configs as a list of dicts."""
        return provisioning.list_cloud_storage(self.root_dir, self.participant_hex)

    def add_cloud_storage(self, protocol, url, access_key=None, secret_key=None,
                          client_id=None, client_secret=None,
                          refresh_token=None, access_token=None, token_expiry=None):
        """Add a cloud storage configuration."""
        provisioning.add_cloud_storage(
            self.root_dir, self.participant_hex,
            protocol=protocol, url=url,
            access_key=access_key, secret_key=secret_key,
            client_id=client_id, client_secret=client_secret,
            refresh_token=refresh_token, access_token=access_token,
            token_expiry=token_expiry,
        )

    def remove_cloud_storage(self, storage_id_hex):
        """Remove a cloud storage config by its hex ID."""
        provisioning.remove_cloud_storage(self.root_dir, self.participant_hex, storage_id_hex)

    def connect(self, team="NoteToSelf", pin_provider=None, mode: str = "encrypted"):
        """Open a Hub session for cloud sync on the given team berth.

        pin_provider: callable(pending_id) → pin string.  The Hub sends the PIN
        via OS notification; pin_provider is responsible for collecting it from
        the user and returning it.  Pass a backend-aware lambda in tests.
        Raises RuntimeError if pin_provider is None (no production default yet).
        """
        pending_id = self.client.request_session(
            self.participant_hex,
            "SmallSeaCollectiveCore",
            team,
            "TeamManager",
            mode=mode,
        )
        if pin_provider is None:
            raise RuntimeError(
                "connect() requires a pin_provider callable(pending_id) → pin. "
                "Approve the session via the Hub UI or pass pin_provider in tests."
            )
        pin = pin_provider(pending_id)
        self.session = self.client.confirm_session(pending_id, pin)

    # --- Team CRUD ---

    def create_team(self, team_name):
        """Create a new team."""
        return provisioning.create_team(self.root_dir, self.participant_hex, team_name)

    def list_teams(self):
        """List all teams the current participant belongs to."""
        return provisioning.list_teams(self.root_dir, self.participant_hex)

    def get_team(self, team_name):
        """Get details for a specific team."""
        members = provisioning.list_members(self.root_dir, self.participant_hex, team_name)
        invitations = provisioning.list_invitations(self.root_dir, self.participant_hex, team_name)
        return {
            "name": team_name,
            "members": members,
            "invitations": invitations,
        }

    def delete_team(self, team_name):
        """Delete a team. Must be an admin."""
        raise NotImplementedError("delete_team")

    # --- Members ---

    def list_members(self, team_name):
        """List members of a team."""
        return provisioning.list_members(self.root_dir, self.participant_hex, team_name)

    def remove_member(self, team_name, member):
        """Remove a member from a team. Must be an admin."""
        raise NotImplementedError("remove_member")

    def set_member_role(self, team_name, member, role):
        """Set a member's role (admin or observer)."""
        if role not in ("admin", "observer"):
            raise ValueError(f"Unknown role: {role}. Must be 'admin' or 'observer'.")
        raise NotImplementedError("set_member_role")

    # --- Invitations ---

    def create_invitation(self, team_name, invitee_label=None, role="admin"):
        """Create an invitation token for someone to join a team."""
        cloud = provisioning.get_cloud_storage(self.root_dir, self.participant_hex)
        return provisioning.create_invitation(
            self.root_dir, self.participant_hex, team_name, cloud,
            invitee_label=invitee_label, role=role,
        )

    def list_invitations(self, team_name):
        """List invitations for a team."""
        return provisioning.list_invitations(self.root_dir, self.participant_hex, team_name)

    def revoke_invitation(self, team_name, invitation_id):
        """Revoke a pending invitation."""
        provisioning.revoke_invitation(
            self.root_dir, self.participant_hex, team_name, invitation_id
        )

    def accept_invitation(self, token_b64):
        """Accept an invitation token (acceptor side). Returns an acceptance token.

        Opens a NoteToSelf Hub session to proxy the inviter's cloud for cloning.
        The push to the acceptor's own cloud is a separate step — call push_team()
        after establishing a team session via the UI.
        The Hub is never bypassed — all cloud I/O goes through it.
        """
        import base64 as _b64
        import json as _json
        from cod_sync.protocol import ExplicitProxyRemote

        token = _json.loads(_b64.b64decode(token_b64))
        inviter_cloud = token["inviter_cloud"]
        inviter_bucket = token["inviter_bucket"]
        inviter_sender_key = provisioning.deserialize_distribution_message(
            token["inviter_sender_key"]
        )

        # NoteToSelf session to access /cloud_proxy for the inviter's bucket.
        # The acceptor doesn't have a team session yet — NoteToSelf provides auth.
        nts_session = self._get_or_open_session(
            "SmallSeaCollectiveCore", "NoteToSelf", mode="passthrough"
        )
        http = self.client._http_client  # None in production; injected TestClient in tests
        proxy_remote = ExplicitProxyRemote(
            nts_session.token,
            inviter_cloud["protocol"],
            inviter_cloud["url"],
            inviter_bucket,
            base_url=self.client._base_url,
            client=http,
            download_transform=lambda payload: provisioning.decrypt_invitation_bootstrap_payload(
                inviter_sender_key, payload
            ),
        )

        # Clone + local DB writes. No cloud push happens here.
        return provisioning.accept_invitation(
            self.root_dir, self.participant_hex, token_b64,
            inviter_remote=proxy_remote,
        )

    def _team_repo_dir(self, team_name: str) -> pathlib.Path:
        return self.root_dir / "Participants" / self.participant_hex / team_name / "Sync"

    def _git_head(self, repo_dir: pathlib.Path) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            return result.stdout.strip()
        except Exception:
            return None

    def _push_status_file(self, team_name: str) -> pathlib.Path:
        # Stored alongside (not inside) the Sync git repo to avoid polluting it.
        return self.root_dir / "Participants" / self.participant_hex / team_name / ".ss_last_push"

    def get_team_sync_status(self, team_name: str) -> str:
        """Return 'synced', 'needs_push', or 'never_pushed'."""
        repo_dir = self._team_repo_dir(team_name)
        current_head = self._git_head(repo_dir)
        if current_head is None:
            return "never_pushed"
        status_file = self._push_status_file(team_name)
        if not status_file.exists():
            return "never_pushed"
        last_pushed = status_file.read_text().strip()
        return "synced" if current_head == last_pushed else "needs_push"

    def push_team(self, team_name):
        """Push the team's Sync repo to the participant's cloud bucket.

        Opens a Hub session internally — works in auto-approve mode without a
        PIN provider.  Raises RuntimeError on CAS conflict (cloud is ahead;
        pull from peers first).
        """
        from cod_sync.protocol import CodSync, SmallSeaRemote, CasConflictError

        session = self._get_or_open_session("SmallSeaCollectiveCore", team_name)
        session.ensure_cloud_ready()
        repo_dir = self._team_repo_dir(team_name)
        remote = SmallSeaRemote(session.token, base_url=self.client._base_url)
        cs = CodSync("origin", repo_dir=repo_dir)
        cs.remote = remote
        try:
            cs.push_to_remote(["main"])
        except CasConflictError:
            raise RuntimeError(
                "Push conflict — cloud is ahead of local. Pull from peers first."
            )
        head = self._git_head(repo_dir)
        if head:
            self._push_status_file(team_name).write_text(head)

    def complete_invitation_acceptance(self, team_name, acceptance_b64):
        """Complete an acceptance (inviter side): add acceptor as member + peer."""
        provisioning.complete_invitation_acceptance(
            self.root_dir, self.participant_hex, team_name, acceptance_b64
        )

    # --- Notification services ---

    def set_notification_service(self, protocol, url, access_key=None, access_token=None):
        """Upsert a notification service in this participant's NoteToSelf DB.

        Replaces any existing row with the same protocol, so safe to call
        repeatedly (e.g. to update the URL of an existing ntfy server).
        Returns the new notification service ID hex.
        """
        return provisioning.set_notification_service(
            self.root_dir, self.participant_hex, protocol, url,
            access_key=access_key, access_token=access_token,
        )

    # --- Sync ---

    def push(self, repo_dir) -> PushResult:
        """Push the local git repo to this participant's cloud bucket.

        Requires an active session (call connect() first).
        Returns PushResult(ok=False, reason="behind") if the cloud has moved on
        since the last push (CAS conflict); the caller should pull and retry.
        """
        from cod_sync.protocol import CodSync, SmallSeaRemote, CasConflictError

        assert self.session is not None, "call connect() before push()"

        self.session.ensure_cloud_ready()

        remote = SmallSeaRemote(self.session.token, base_url=self.client._base_url)

        cs = CodSync("origin", repo_dir=pathlib.Path(repo_dir))
        cs.remote = remote
        try:
            cs.push_to_remote(["main"])
        except CasConflictError:
            return PushResult(ok=False, reason="behind")
        return PushResult(ok=True)

    def pull(self, repo_dir, from_member_id: str) -> PullResult:
        """Fetch and merge from a peer's cloud bucket.

        Requires an active session (call connect() first).
        Returns PullResult(has_conflicts=True) if git merge left unresolved conflicts.
        """
        from cod_sync.protocol import CodSync, PeerSmallSeaRemote

        assert self.session is not None, "call connect() before pull()"

        remote = PeerSmallSeaRemote(
            self.session.token, from_member_id, base_url=self.client._base_url
        )

        cs = CodSync("peer", repo_dir=pathlib.Path(repo_dir))
        cs.remote = remote
        if cs.fetch_from_remote(["main"]) is None:
            return PullResult(has_conflicts=True)
        exit_code = cs.merge_from_remote(["main"])
        return PullResult(has_conflicts=(exit_code != 0))
