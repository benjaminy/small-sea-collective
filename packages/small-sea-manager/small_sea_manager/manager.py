# Top Matter

import pathlib
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
        self.client = SmallSeaClient(port=hub_port, _http_client=_http_client)
        self.session = None

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

    def connect(self, team="NoteToSelf", pin_provider=None):
        """Open a Hub session for cloud sync on the given team station.

        pin_provider: callable(pending_id) → pin string.  The Hub sends the PIN
        via OS notification; pin_provider is responsible for collecting it from
        the user and returning it.  Pass a backend-aware lambda in tests.
        Raises RuntimeError if pin_provider is None (no production default yet).
        """
        pending_id = self.client.request_session(
            self.participant_hex, "SmallSeaCollectiveCore", team, "TeamManager"
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

        Opens a NoteToSelf Hub session to proxy the inviter's cloud for cloning,
        then pushes the new team repo to the acceptor's own cloud via a team session.
        The Hub is never bypassed — all cloud I/O goes through it.
        """
        import base64 as _b64
        import json as _json
        from cod_sync.protocol import CodSync, ExplicitProxyRemote, SmallSeaRemote, CasConflictError

        token = _json.loads(_b64.b64decode(token_b64))
        team_name = token["team_name"]
        inviter_cloud = token["inviter_cloud"]
        inviter_bucket = token["inviter_bucket"]

        # Open NoteToSelf session to access /cloud_proxy for the inviter's bucket.
        # Bob doesn't have a team session yet — the NoteToSelf session provides auth.
        nts_session = self.client.open_session(
            self.participant_hex, "SmallSeaCollectiveCore", "NoteToSelf", "TeamManager"
        )
        http = self.client._http_client  # None in production; injected TestClient in tests
        proxy_remote = ExplicitProxyRemote(
            nts_session.token,
            inviter_cloud["protocol"],
            inviter_cloud["url"],
            inviter_bucket,
            base_url=self.client._base_url,
            client=http,
        )

        # Clone + local DB writes. No cloud push happens inside provisioning.
        acceptance_b64 = provisioning.accept_invitation(
            self.root_dir, self.participant_hex, token_b64,
            inviter_remote=proxy_remote,
        )

        # Push to acceptor's own cloud via a team Hub session.
        team_session = self.client.open_session(
            self.participant_hex, "SmallSeaCollectiveCore", team_name, "TeamManager"
        )
        team_session.ensure_cloud_ready()
        team_repo_dir = (
            self.root_dir / "Participants" / self.participant_hex / team_name / "Sync"
        )
        remote = SmallSeaRemote(team_session.token, base_url=self.client._base_url, client=http)
        cs = CodSync("origin", repo_dir=team_repo_dir)
        cs.remote = remote
        try:
            cs.push_to_remote(["main"])
        except CasConflictError:
            raise RuntimeError("CAS conflict pushing new team repo — unexpected on first push")

        return acceptance_b64

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
        cs.fetch_from_remote(["main"])
        exit_code = cs.merge_from_remote(["main"])
        return PullResult(has_conflicts=(exit_code != 0))
