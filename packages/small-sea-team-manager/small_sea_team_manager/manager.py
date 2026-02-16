# Top Matter

from small_sea_client.client import SmallSeaClient


class TeamManager:
    """Business logic for team management operations.

    This is the shared core used by both the CLI and the web UI.
    All methods talk to the Hub through the regular SmallSeaClient interface.
    """

    def __init__(self, hub_port=11437):
        self.client = SmallSeaClient(port=hub_port)
        self.session = None
        self.nickname = None

    def connect(self, nickname, team="NoteToSelf"):
        """Open a session with the Hub for team management."""
        self.nickname = nickname
        self.session = self.client.open_session(
            nickname,
            "SmallSeaCollectiveCore",
            team,
            "TeamManager")

    # --- Team CRUD ---

    def create_team(self, team_name):
        """Create a new team."""
        # TODO: call session.create_new_team once Hub supports it properly
        raise NotImplementedError("create_team")

    def list_teams(self):
        """List all teams the current user belongs to."""
        # TODO: query the Hub for team list
        # Stub: return placeholder data
        return [
            {"name": "NoteToSelf", "role": "admin", "members": 1},
        ]

    def get_team(self, team_name):
        """Get details for a specific team."""
        # TODO: query the Hub for team details
        return {
            "name": team_name,
            "members": [],
            "invitations": [],
        }

    def delete_team(self, team_name):
        """Delete a team. Must be an admin."""
        raise NotImplementedError("delete_team")

    # --- Members ---

    def list_members(self, team_name):
        """List members of a team."""
        # TODO: query the Team-SmallSeaCore station for membership records
        # Stub: return placeholder data
        return [
            {"nickname": self.nickname or "unknown", "role": "admin"},
        ]

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
        # TODO: query invitation records from the Team-SmallSeaCore station
        return []

    def revoke_invitation(self, team_name, invitation_id):
        """Revoke a pending invitation."""
        raise NotImplementedError("revoke_invitation")

    def accept_invitation(self, invitation_id):
        """Accept an invitation to join a team."""
        raise NotImplementedError("accept_invitation")
