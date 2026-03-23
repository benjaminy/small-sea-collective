# Top Matter

import pathlib

from small_sea_client.client import SmallSeaClient
from small_sea_manager import provisioning


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

    def create_invitation(self, team_name, invitee):
        """Create an invitation for someone to join a team."""
        raise NotImplementedError("create_invitation")

    def list_invitations(self, team_name):
        """List pending invitations for a team."""
        return provisioning.list_invitations(self.root_dir, self.participant_hex, team_name)

    def revoke_invitation(self, team_name, invitation_id):
        """Revoke a pending invitation."""
        raise NotImplementedError("revoke_invitation")

    def accept_invitation(self, invitation_id):
        """Accept an invitation to join a team."""
        raise NotImplementedError("accept_invitation")
