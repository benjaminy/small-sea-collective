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

    def __init__(self, root_dir, participant_hex, hub_port=11437):
        self.root_dir = pathlib.Path(root_dir)
        self.participant_hex = participant_hex
        self.client = SmallSeaClient(port=hub_port)
        self.session = None

    def get_nickname(self):
        """Return the participant's first nickname, or a short hex fallback."""
        return provisioning.get_nickname(self.root_dir, self.participant_hex)

    def _cloud(self):
        """Return the participant's primary cloud storage config dict."""
        return provisioning.get_cloud_storage(self.root_dir, self.participant_hex)

    def connect(self, team="NoteToSelf"):
        """Open a Hub session for cloud sync on the given team station."""
        self.session = self.client.open_session(
            self.participant_hex, "SmallSeaCollectiveCore", team, "TeamManager"
        )

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

    def accept_invitation(self, token_b64, inviter_remote, acceptor_remote):
        """Accept an invitation token (acceptor side). Returns an acceptance token.

        inviter_remote: CodSyncRemote for reading the inviter's public bucket.
        acceptor_remote: CodSyncRemote for writing to the acceptor's own bucket.
        """
        return provisioning.accept_invitation(
            self.root_dir, self.participant_hex, token_b64,
            inviter_remote, acceptor_remote,
        )

    def complete_invitation_acceptance(self, team_name, acceptance_b64):
        """Complete an acceptance (inviter side): add acceptor as member + peer."""
        provisioning.complete_invitation_acceptance(
            self.root_dir, self.participant_hex, team_name, acceptance_b64
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
