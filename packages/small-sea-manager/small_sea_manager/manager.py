import pathlib
from typing import Optional

import cod_sync.protocol as CodSyncProtocol
from cod_sync.repo import Repo as _Repo
from cod_sync.protocol import BootstrapProxyRemote, CodSync, SmallSeaRemote
from small_sea_client.client import SmallSeaClient
from small_sea_manager import provisioning

_CORE_APP = "SmallSeaCollectiveCore"


def create_identity_join_request(root_dir):
    """Create a public join-request artifact for a blank installation."""
    return provisioning.create_identity_join_request(root_dir)


def bootstrap_existing_identity(root_dir, welcome_bundle_b64, hub_port=11437, _http_client=None):
    """Bootstrap a blank installation into an existing identity."""
    prepared = provisioning.prepare_identity_bootstrap(root_dir, welcome_bundle_b64)
    bundle = prepared["bundle"]
    sync_dir = pathlib.Path(prepared["sync_dir"])

    remote = bundle.remote_descriptor
    if remote["protocol"] == "localfolder":
        cod = CodSync("bootstrap-identity", repo_dir=sync_dir)
        cod.remote = provisioning._remote_from_descriptor(remote)
    else:
        client = SmallSeaClient(port=hub_port, _http_client=_http_client)
        bootstrap_token = client.create_bootstrap_session(
            protocol=remote["protocol"],
            url=remote["url"],
            bucket=remote["bucket"],
            expires_at=bundle.expires_at,
        )
        cod = CodSync("bootstrap-identity", repo_dir=sync_dir)
        cod.remote = BootstrapProxyRemote(
            bootstrap_token,
            base_url=client._base_url,
            client=_http_client,
        )

    fetched_sha = cod.fetch_from_remote(["main"])
    if fetched_sha is None:
        raise RuntimeError("Failed to fetch NoteToSelf during identity bootstrap")
    CodSyncProtocol.gitCmd(["-C", str(sync_dir), "checkout", "main"])
    return provisioning.finalize_identity_bootstrap(root_dir, prepared)


class TeamManager:
    """Business logic for team management operations.

    Reads team/member/invitation data directly from the local SQLite DB.
    Hub sessions (via SmallSeaClient) are used only for cloud sync operations.
    """

    def __init__(self, root_dir, participant_hex, hub_port=11437, _http_client=None):
        self.root_dir = pathlib.Path(root_dir)
        self.participant_hex = participant_hex
        provisioning.assert_identity_bootstrap_trusted(self.root_dir, self.participant_hex)
        provisioning.migrate_participant_team_dbs(self.root_dir, self.participant_hex)
        self.client = SmallSeaClient(port=hub_port, _http_client=_http_client)
        # Confirmed sessions, keyed by (team, mode).
        self._sessions: dict[tuple[str, str], "SmallSeaSession"] = {}
        # Pending PIN requests awaiting confirmation, keyed by (team, mode).
        self._pending: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------ #
    # Session state management
    # ------------------------------------------------------------------ #

    def set_session(self, team: str, token: str, mode: str = "encrypted") -> None:
        """Store a confirmed session token for (team, mode)."""
        from small_sea_client.client import SmallSeaSession
        key = (team, mode)
        self._sessions[key] = SmallSeaSession(self.client, token)
        self._pending.pop(key, None)

    def clear_session(self, team: str, mode: str = "encrypted") -> None:
        """Remove the confirmed session for (team, mode)."""
        self._sessions.pop((team, mode), None)

    def set_pending(self, team: str, pending_id: str, mode: str = "encrypted") -> None:
        """Record a pending PIN request for (team, mode)."""
        self._pending[(team, mode)] = pending_id

    def clear_pending(self, team: str, mode: str = "encrypted") -> None:
        self._pending.pop((team, mode), None)

    def session_state(self, team: str, mode: str = "encrypted") -> str:
        """Return 'active', 'pending', or 'none' for the given (team, mode)."""
        key = (team, mode)
        if key in self._sessions:
            return "active"
        if key in self._pending:
            return "pending"
        return "none"

    def get_pending_id(self, team: str, mode: str = "encrypted") -> str | None:
        return self._pending.get((team, mode))

    def _get_or_open_session(self, team: str, mode: str = "encrypted") -> "SmallSeaSession":
        """Return a confirmed session for (team, mode).

        Uses the cached session if one has been established (e.g. via PIN
        flow). Otherwise opens a new session via open_session, which requires
        the Hub to be in auto-approve mode.
        """
        key = (team, mode)
        if key in self._sessions:
            return self._sessions[key]
        return self.client.open_session(
            self.participant_hex, _CORE_APP, team, "TeamManager", mode=mode
        )

    def get_nickname(self):
        """Return the participant's first nickname, or a short hex fallback."""
        return provisioning.get_nickname(self.root_dir, self.participant_hex)

    def _cloud(self):
        """Return the participant's primary cloud storage config dict."""
        return provisioning.get_cloud_storage(self.root_dir, self.participant_hex)

    def _note_to_self_repo_dir(self) -> pathlib.Path:
        return self.root_dir / "Participants" / self.participant_hex / "NoteToSelf" / "Sync"

    def _open_note_to_self_session(self, mode: str = "passthrough"):
        return self._get_or_open_session("NoteToSelf", mode=mode)

    def _note_to_self_remote_descriptor(self) -> dict:
        cloud = self._cloud()
        if cloud["protocol"] == "localfolder":
            return {
                "protocol": "localfolder",
                "url": cloud["url"],
            }
        if cloud["protocol"] != "s3":
            raise ValueError(
                f"Unsupported identity bootstrap provider: {cloud['protocol']}"
            )
        nts_session = self._open_note_to_self_session(mode="passthrough")
        session_info = nts_session.session_info()
        berth_id = session_info["berth_id"]
        return {
            "protocol": cloud["protocol"],
            "url": cloud["url"],
            "bucket": f"ss-{berth_id[:16]}",
        }

    def _ensure_note_to_self_adopted_count(self, session) -> tuple[bytes, int]:
        berth_id = bytes.fromhex(session.session_info()["berth_id"])
        adopted = provisioning.get_note_to_self_adopted_signal_count(
            self.root_dir, self.participant_hex, berth_id
        )
        if adopted is None:
            snapshot = session.watch_notifications({}, timeout=0, known_self_count=0)
            adopted = int(snapshot.get("self_updated_count") or 0)
            provisioning.set_note_to_self_adopted_signal_count(
                self.root_dir, self.participant_hex, berth_id, adopted
            )
        return berth_id, adopted

    def push_note_to_self(self):
        """Push the NoteToSelf Sync repo to the participant's cloud bucket.

        Stages and commits any outstanding changes to core.db before pushing
        so that NoteToSelf mutations (e.g. new team rows from create_team) are
        included in the push without requiring callers to commit explicitly.
        """
        session = self._open_note_to_self_session(mode="passthrough")
        berth_id, adopted = self._ensure_note_to_self_adopted_count(session)
        session.ensure_cloud_ready()
        repo_dir = self._note_to_self_repo_dir()
        # Stage and commit any uncommitted NoteToSelf DB changes.
        nts_repo = _Repo(repo_dir / ".git", repo_dir)
        nts_repo.stage(["core.db"])
        nts_repo.commit("Update NoteToSelf")
        remote = SmallSeaRemote(session.token, base_url=self.client._base_url, client=self.client._http_client)
        cs = CodSync("origin", repo_dir=repo_dir)
        cs.remote = remote
        cs.push_to_remote(["main"])
        provisioning.set_note_to_self_adopted_signal_count(
            self.root_dir,
            self.participant_hex,
            berth_id,
            adopted + 1,
        )

    def refresh_note_to_self(self):
        """Fetch and adopt shared NoteToSelf updates through the Hub transport."""
        session = self._open_note_to_self_session(mode="passthrough")
        berth_id, adopted = self._ensure_note_to_self_adopted_count(session)
        repo_dir = self._note_to_self_repo_dir()
        remote = SmallSeaRemote(
            session.token, base_url=self.client._base_url, client=self.client._http_client
        )
        cs = CodSync("origin", repo_dir=repo_dir)
        cs.remote = remote
        # Snapshot the berth counter BEFORE the fetch so the adopted baseline
        # only advances to state we've actually incorporated. Reading the counter
        # after the merge could observe a later push (counter N+1 or N+2) that
        # this device has not yet fetched, causing that push to be silently
        # skipped on the next watch/refresh cycle.
        pre_fetch_snapshot = session.watch_notifications({}, timeout=0, known_self_count=adopted)
        pre_fetch_count = int(pre_fetch_snapshot.get("self_updated_count") or adopted)
        fetched_sha = cs.fetch_from_remote(["main"])
        if fetched_sha is None:
            raise RuntimeError("Failed to fetch NoteToSelf from remote")
        merge_result = cs.merge_from_remote(["main"])
        if merge_result != 0:
            raise RuntimeError(f"Failed to adopt refreshed NoteToSelf (merge exit {merge_result})")
        provisioning.set_note_to_self_adopted_signal_count(
            self.root_dir,
            self.participant_hex,
            berth_id,
            pre_fetch_count,
        )
        new_count = pre_fetch_count
        return {
            "berth_id": berth_id.hex(),
            "adopted_count": new_count,
            "teams": self.list_known_teams(),
        }

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

    # --- Team CRUD ---

    def create_team(self, team_name):
        """Create a new team."""
        return provisioning.create_team(self.root_dir, self.participant_hex, team_name)

    def list_teams(self):
        """List all teams the current participant belongs to."""
        return self.list_known_teams()

    def list_known_teams(self):
        """List teams known from shared NoteToSelf, whether or not joined locally."""
        teams = provisioning.list_teams(self.root_dir, self.participant_hex)
        for team in teams:
            team["joined_locally"] = provisioning.has_local_team_clone(
                self.root_dir,
                self.participant_hex,
                team["name"],
            )
        return teams

    def get_team(self, team_name):
        """Get details for a specific team."""
        joined_locally = provisioning.has_local_team_clone(
            self.root_dir, self.participant_hex, team_name
        )
        if not joined_locally:
            return {
                "name": team_name,
                "joined_locally": False,
                "members": [],
                "invitations": [],
            }
        members = provisioning.list_members(self.root_dir, self.participant_hex, team_name)
        invitations = provisioning.list_invitations(self.root_dir, self.participant_hex, team_name)
        return {
            "name": team_name,
            "joined_locally": True,
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
        return provisioning.remove_member(
            self.root_dir,
            self.participant_hex,
            team_name,
            member,
        )

    def reconcile_runtime_state(self, team_name):
        """Reconcile local runtime state against the adopted team view."""
        return provisioning.reconcile_runtime_state(
            self.root_dir,
            self.participant_hex,
            team_name,
        )

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

    def authorize_identity_join(self, join_request_artifact_b64, *, expires_in_seconds=600):
        """Admit a new device into this participant's NoteToSelf identity."""
        remote_descriptor = self._note_to_self_remote_descriptor()
        result = provisioning.authorize_identity_join(
            self.root_dir,
            self.participant_hex,
            join_request_artifact_b64,
            remote_descriptor=remote_descriptor,
            expires_in_seconds=expires_in_seconds,
        )
        if result.get("needs_publish"):
            self.push_note_to_self()
        return result

    def prepare_linked_device_team_join(self, team_name):
        """Prepare the joining-device side of same-member encrypted team bootstrap."""
        return provisioning.prepare_linked_device_team_join(
            self.root_dir,
            self.participant_hex,
            team_name,
        )

    def create_linked_device_bootstrap(self, team_name, join_request_bundle):
        """Authorize a linked-device encrypted team bootstrap."""
        return provisioning.create_linked_device_bootstrap(
            self.root_dir,
            self.participant_hex,
            team_name,
            join_request_bundle,
        )

    def finalize_linked_device_bootstrap(self, team_name, bootstrap_bundle):
        """Finalize the joining-device side of encrypted team bootstrap."""
        return provisioning.finalize_linked_device_bootstrap(
            self.root_dir,
            self.participant_hex,
            team_name,
            bootstrap_bundle,
        )

    def complete_linked_device_bootstrap(self, team_name, sender_distribution_payload):
        """Store the linked device's sender distribution on the authorizing device."""
        return provisioning.complete_linked_device_bootstrap(
            self.root_dir,
            self.participant_hex,
            team_name,
            sender_distribution_payload,
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
        inviter_sender_key_state = provisioning.deserialize_sender_key_record(
            token["inviter_sender_key"]
        )

        def _decrypt_payload(payload):
            nonlocal inviter_sender_key_state
            inviter_sender_key_state, plaintext = (
                provisioning.decrypt_invitation_bootstrap_payload(
                    inviter_sender_key_state, payload
                )
            )
            return plaintext

        # NoteToSelf session to access /cloud_proxy for the inviter's bucket.
        # The acceptor doesn't have a team session yet — NoteToSelf provides auth.
        nts_session = self._get_or_open_session("NoteToSelf", mode="passthrough")
        http = self.client._http_client  # None in production; injected TestClient in tests
        proxy_remote = ExplicitProxyRemote(
            nts_session.token,
            inviter_cloud["protocol"],
            inviter_cloud["url"],
            inviter_bucket,
            base_url=self.client._base_url,
            client=http,
            download_transform=_decrypt_payload,
        )

        # Clone + local DB writes. No cloud push happens here.
        return provisioning.accept_invitation(
            self.root_dir, self.participant_hex, token_b64,
            inviter_remote=proxy_remote,
        )

    def _team_repo_dir(self, team_name: str) -> pathlib.Path:
        return self.root_dir / "Participants" / self.participant_hex / team_name / "Sync"

    def _git_head(self, repo_dir: pathlib.Path) -> Optional[str]:
        return _Repo(repo_dir / ".git", repo_dir).head()

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
        from cod_sync.protocol import CasConflictError

        session = self._get_or_open_session(team_name)
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
