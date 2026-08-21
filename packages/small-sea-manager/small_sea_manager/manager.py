import logging
import pathlib
from typing import Optional

from cod_sync.repo import Repo as _Repo
from cod_sync.protocol import CodSync
from cod_sync.store import BootstrapProxyStore, SmallSeaStore
from small_sea_client.client import SmallSeaClient, SmallSeaHubUnavailable
from small_sea_manager import admission_events
from small_sea_manager import provisioning

_CORE_APP = "SmallSeaCollectiveCore"
_LOG = logging.getLogger(__name__)


class AppSightingsRefresh(list):
    """List of current app-bootstrap prompts, with an optional cleanup warning.

    Behaves as a normal list so existing callers can iterate and index. The
    ``cleanup_warning`` attribute is non-None when any per-row clear or the
    stale prune call failed during the refresh. Web/UI layers should render
    the prompts and surface the warning together.
    """

    def __init__(self, prompts, *, cleanup_warning=None):
        super().__init__(prompts)
        self.cleanup_warning = cleanup_warning


def create_identity_join_request(root_dir, *, device_label=None):
    """Create a public join-request artifact for a blank installation."""
    return provisioning.create_identity_join_request(root_dir, device_label=device_label)


def bootstrap_existing_identity(root_dir, welcome_bundle_b64, hub_port=11437, _http_client=None):
    """Bootstrap a blank installation into an existing identity."""
    prepared = provisioning.prepare_identity_bootstrap(root_dir, welcome_bundle_b64)
    bundle = prepared["bundle"]
    sync_dir = pathlib.Path(prepared["sync_dir"])

    remote = bundle.remote_descriptor
    if remote["protocol"] == "localfolder":
        store = provisioning._store_from_descriptor(remote)
    else:
        client = SmallSeaClient(port=hub_port, _http_client=_http_client)
        bootstrap_token = client.create_bootstrap_session(
            protocol=remote["protocol"],
            url=remote["url"],
            bucket=remote["bucket"],
            expires_at=bundle.expires_at,
        )
        store = BootstrapProxyStore(
            bootstrap_token,
            base_url=client._base_url,
            client=_http_client,
        )

    repo = _Repo(sync_dir / ".git", sync_dir)
    result = CodSync(repo, store).fetch()
    repo.checkout_branch("main", start_point=result.observed_head)
    return provisioning.finalize_identity_bootstrap(root_dir, prepared)


class TeamManager:
    """Business logic for team management operations.

    Reads team/teammate/invitation data directly from the local SQLite DB.
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
        allocation = provisioning.get_berth_cloud_allocation_for_berth(
            self.root_dir,
            self.participant_hex,
            berth_id,
        )
        if allocation is None:
            allocation = provisioning.add_berth_cloud_allocation_by_berth_id(
                self.root_dir,
                self.participant_hex,
                berth_id,
                cloud["id"],
            )
        return {
            "protocol": cloud["protocol"],
            "url": cloud["url"],
            "bucket": allocation["location"],
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

        Unlike push_team, this still commits the whole index. The analogous
        path-scoping change belongs with the NoteToSelf multi-device work.
        """
        session = self._open_note_to_self_session(mode="passthrough")
        berth_id, adopted = self._ensure_note_to_self_adopted_count(session)
        session.ensure_cloud_ready()
        repo_dir = self._note_to_self_repo_dir()
        # Stage and commit any uncommitted NoteToSelf DB changes.
        nts_repo = _Repo(repo_dir / ".git", repo_dir)
        nts_repo.stage(["core.db"])
        nts_repo.commit("Update NoteToSelf")
        store = SmallSeaStore(
            session.token, base_url=self.client._base_url, client=self.client._http_client
        )
        result = CodSync(_Repo(repo_dir / ".git", repo_dir), store).publish()
        if result.disposition != "published":
            # already_present sends no Hub notification, so there is no new
            # self-signal for the adopted baseline to catch up to. That holds
            # whether the stored head equals this one or already descends from
            # it: the incoming gap is refresh_note_to_self's business.
            return
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
        store = SmallSeaStore(
            session.token, base_url=self.client._base_url, client=self.client._http_client
        )
        repo = _Repo(repo_dir / ".git", repo_dir)
        cs = CodSync(repo, store)
        # Snapshot the berth counter BEFORE the fetch so the adopted baseline
        # only advances to state we've actually incorporated. Reading the counter
        # after the merge could observe a later push (counter N+1 or N+2) that
        # this device has not yet fetched, causing that push to be silently
        # skipped on the next watch/refresh cycle.
        pre_fetch_snapshot = session.watch_notifications({}, timeout=0, known_self_count=adopted)
        pre_fetch_count = int(pre_fetch_snapshot.get("self_updated_count") or adopted)
        result = cs.fetch()
        if repo.has_commits():
            repo.merge(result.observed_head)
        else:
            repo.checkout_branch("main", start_point=result.observed_head)
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
                "teammates": [],
                "invitations": [],
                "admission_events": [],
                "viewer_is_steward": False,
                "self_in_team": None,
            }
        teammates = provisioning.list_teammates(self.root_dir, self.participant_hex, team_name)
        invitations = provisioning.list_invitations(self.root_dir, self.participant_hex, team_name)
        self_in_team = provisioning.get_self_in_team(
            self.root_dir,
            self.participant_hex,
            team_name,
        )
        viewer_is_steward = False
        if self_in_team is not None:
            for teammate in teammates:
                if teammate["id"] != self_in_team:
                    continue
                roles = teammate.get("berth_roles", [])
                viewer_is_steward = any(role["role"] == "read-write" for role in roles)
                break
        return {
            "name": team_name,
            "joined_locally": True,
            "teammates": teammates,
            "invitations": invitations,
            "admission_events": admission_events.list_admission_events(
                self.root_dir,
                self.participant_hex,
                team_name,
                self_teammate_id_hex=self_in_team,
                viewer_is_steward=viewer_is_steward,
            ),
            "viewer_is_steward": viewer_is_steward,
            "self_in_team": self_in_team,
        }

    def delete_team(self, team_name):
        """Delete a team. Must be a steward."""
        raise NotImplementedError("delete_team")

    # --- Teammates ---

    def list_teammates(self, team_name):
        """List teammates of a team."""
        return provisioning.list_teammates(self.root_dir, self.participant_hex, team_name)

    def remove_teammate(self, team_name, teammate):
        """Remove a teammate from a team. Must be a steward."""
        return provisioning.remove_teammate(
            self.root_dir,
            self.participant_hex,
            team_name,
            teammate,
        )

    def announce_teammate_transport(self, team_name, *, protocol: str, url: str, bucket: str):
        """Publish a signed transport announcement for the current teammate."""
        return provisioning.announce_teammate_transport(
            self.root_dir,
            self.participant_hex,
            team_name,
            protocol=protocol,
            url=url,
            bucket=bucket,
        )

    def publish_teammate_berth_storage_announcement(self, team_name, berth_id, allocation):
        """Publish this teammate's storage location for one berth."""
        team = provisioning._team_row(self.root_dir, self.participant_hex, team_name)
        return provisioning.publish_teammate_berth_storage_announcement(
            self.root_dir,
            self.participant_hex,
            team_name,
            team[1],
            berth_id,
            allocation,
        )

    def reconcile_runtime_state(self, team_name):
        """Reconcile local runtime state against the adopted team view."""
        return provisioning.reconcile_runtime_state(
            self.root_dir,
            self.participant_hex,
            team_name,
        )

    def set_teammate_role(self, team_name, teammate, role):
        """Set a teammate's role (steward or contributor)."""
        if role not in ("steward", "contributor"):
            raise ValueError(f"Unknown role: {role}. Must be 'steward' or 'contributor'.")
        raise NotImplementedError("set_teammate_role")

    # --- Invitations ---

    def create_invitation(self, team_name, invitee_label=None, role="steward"):
        """Create an invitation token for someone to join a team.

        `role` is the UI preset (steward/contributor); it is translated to the
        per-berth mode-plan expansion rule at this boundary.
        """
        cloud = provisioning.get_cloud_storage(self.root_dir, self.participant_hex)
        return provisioning.create_invitation(
            self.root_dir, self.participant_hex, team_name, cloud,
            invitee_label=invitee_label,
            mode_plan=provisioning.mode_plan_for_preset(role),
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
        """Prepare the joining-device side of same-teammate encrypted team bootstrap."""
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
        """Deprecated bootstrap step retained only to surface a clear error."""
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

    def dismiss_admission_event(self, team_name, event_type, artifact_id_hex):
        """Dismiss an admission event locally for this team clone."""
        admission_events.AdmissionEventType(event_type)
        bytes.fromhex(artifact_id_hex)
        provisioning.dismiss_admission_event(
            self.root_dir,
            self.participant_hex,
            team_name,
            event_type,
            artifact_id_hex,
        )

    def refresh_app_sightings(self):
        """Read Hub app-bootstrap sightings, clear resolved rows, prune stale.

        For each sighting in the listed snapshot:
          - evaluate ``current_app_sighting_prompt`` first (before disposition)
          - if the prompt is ``None``, ask the Hub to clear the row
          - otherwise, drop the prompt iff Manager-local disposition dismissed it

        After the per-row loop, ask the Hub to prune the participant's stale
        rows once. Prompts are computed from the pre-prune snapshot, so a
        long-absent Manager sees stale observations once before they age out.

        Returns a :class:`AppSightingsRefresh` that behaves as a list of
        prompts and also carries a ``cleanup_warning`` string when any
        per-row clear or the prune call failed.
        """
        session = self._open_note_to_self_session()
        snapshot = session.app_sightings()
        prompts = []
        cleanup_failures = 0
        for sighting in snapshot:
            prompt = provisioning.current_app_sighting_prompt(
                self.root_dir,
                self.participant_hex,
                sighting,
            )
            if prompt is None:
                try:
                    session.clear_app_sighting(
                        app_name=sighting["app_name"],
                        team_name=sighting["team_name"],
                        client_name=sighting["client_name"],
                        last_seen_at=sighting["last_seen_at"],
                    )
                except Exception as exc:
                    cleanup_failures += 1
                    _LOG.warning(
                        "clear_app_sighting failed for %s/%s/%s: %s",
                        sighting["team_name"],
                        sighting["app_name"],
                        sighting["client_name"],
                        exc,
                    )
                continue
            if provisioning.app_sighting_dismissed(
                self.root_dir,
                self.participant_hex,
                sighting,
            ):
                continue
            prompts.append(prompt)

        prune_failed = False
        try:
            session.prune_stale_app_sightings()
        except Exception as exc:
            prune_failed = True
            _LOG.warning("prune_stale_app_sightings failed: %s", exc)

        warning = None
        if cleanup_failures or prune_failed:
            parts = []
            if cleanup_failures:
                parts.append(
                    f"could not clear {cleanup_failures} resolved sighting"
                    + ("s" if cleanup_failures != 1 else "")
                )
            if prune_failed:
                parts.append("could not prune stale sightings")
            warning = (
                "Hub cleanup did not finish: "
                + " and ".join(parts)
                + ". Reconnect to Hub and Refresh."
            )
        return AppSightingsRefresh(prompts, cleanup_warning=warning)

    def register_app_for_participant(self, app_name):
        """Register an app for this participant via NoteToSelf."""
        return provisioning.register_app_for_participant(
            self.root_dir,
            self.participant_hex,
            app_name,
        )

    def activate_app_for_team(self, team_name, app_name):
        """Activate an app berth for a team."""
        return provisioning.activate_app_for_team(
            self.root_dir,
            self.participant_hex,
            team_name,
            app_name,
        )

    def dismiss_participant_app_sighting(self, app_name):
        """Suppress participant-level app-bootstrap prompts on this device."""
        provisioning.dismiss_participant_app_sighting(
            self.root_dir,
            self.participant_hex,
            app_name,
        )

    def dismiss_team_app_sighting(self, team_name, app_name):
        """Suppress team-scoped app-bootstrap prompts on this device."""
        provisioning.dismiss_team_app_sighting(
            self.root_dir,
            self.participant_hex,
            team_name,
            app_name,
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
        from cod_sync.store import ExplicitProxyStore

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
        proxy_store = ExplicitProxyStore(
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
            inviter_store=proxy_store,
        )

    def _team_repo_dir(self, team_name: str) -> pathlib.Path:
        return self.root_dir / "Participants" / self.participant_hex / team_name / "Sync"

    def _team_repo(self, team_name: str) -> _Repo:
        repo_dir = self._team_repo_dir(team_name)
        return _Repo(repo_dir / ".git", repo_dir)

    def _push_status_file(self, team_name: str) -> pathlib.Path:
        # Stored alongside (not inside) the Sync git repo to avoid polluting it.
        return self.root_dir / "Participants" / self.participant_hex / team_name / ".ss_last_push"

    def _last_published_head(self, team_name: str) -> Optional[str]:
        status_file = self._push_status_file(team_name)
        if not status_file.exists():
            return None
        return status_file.read_text().strip() or None

    def get_team_sync_status(self, team_name: str) -> str:
        """Return 'synced', 'needs_push', or 'never_pushed'.

        Reports Manager-owned outgoing state only. Incoming state (hinted,
        fetched, parked, conflicted) is tracked separately.

        'never_pushed' means this installation has no successful-publication
        marker, including after a failed first attempt. After a successful
        publication, 'needs_push' covers both an unpublished commit and
        completed but uncommitted Manager-owned Core state. Anything else under
        Sync/ — staged, modified, or untracked — is not Manager-owned
        publication state and does not affect the answer.
        """
        repo = self._team_repo(team_name)
        # Must precede any HEAD-relative check: `git diff HEAD` is fatal in a
        # repo with no commits.
        if repo.head() is None:
            return "never_pushed"
        last_pushed = self._last_published_head(team_name)
        if last_pushed is None:
            return "never_pushed"
        if repo.head() != last_pushed:
            return "needs_push"
        # Same read-only check the publication commit uses, so status and
        # publication can never disagree about what is outstanding.
        if repo.work_tree_paths_differ_from_head(["core.db"]):
            return "needs_push"
        return "synced"

    def push_team(self, team_name) -> str:
        """Publish the team's completed Core state to the participant's cloud.

        Commits outstanding `core.db` work first, so a Manager mutation that
        commits nothing itself cannot stay outside the published chain. Uses a
        path-scoped commit rather than push_note_to_self's whole-index
        stage+commit, which would also publish whatever else already sits in
        the index. Unifying the two idioms is follow-up work.

        Returns Cod Sync's disposition, 'published' or 'already_present'; the
        latter also covers this installation's marker already confirming the
        current HEAD. Opens a Hub session internally — works in auto-approve
        mode without a PIN provider. The three publication states that need
        attention propagate as their typed Cod Sync errors.
        """
        repo_dir = self._team_repo_dir(team_name)
        repo = self._team_repo(team_name)
        repo.commit_paths(["core.db"], "Update team Core")
        # Capture the head being published before the remote operation, so the
        # marker names the exact state Cod Sync was handed.
        intended_head = repo.head()

        # Preparing core.db is purely local, so decide this before opening a
        # session: a no-op publication must not prompt for a PIN. A missing
        # marker does not prove the remote lacks this head, so publication is
        # still attempted in that case.
        if intended_head is not None and self._last_published_head(team_name) == intended_head:
            return "already_present"

        session = self._get_or_open_session(team_name)
        session.ensure_cloud_ready()
        store = SmallSeaStore(
            session.token,
            base_url=self.client._base_url,
            client=self.client._http_client,
        )
        result = CodSync(_Repo(repo_dir / ".git", repo_dir), store).publish()
        # Both ordinary dispositions mean the cloud holds this head — under
        # already_present it may hold a descendant of it — so the marker is
        # written for both. Every other disposition raised before this point
        # and left the marker alone.
        if intended_head:
            self._push_status_file(team_name).write_text(intended_head)
        return result.disposition

    def complete_invitation_acceptance(self, team_name, acceptance_b64):
        """Record invitee acceptance and finalize when quorum is met."""
        provisioning.complete_invitation_acceptance(
            self.root_dir, self.participant_hex, team_name, acceptance_b64
        )

    def endorse_admission(self, team_name, proposal_id):
        """Record this teammate's endorsement of an admission proposal."""
        provisioning.endorse_admission(
            self.root_dir,
            self.participant_hex,
            team_name,
            proposal_id,
        )

    def finalize_admission(self, team_name, proposal_id):
        """Finalize a quorum-met admission proposal as the inviter."""
        provisioning.finalize_admission(
            self.root_dir,
            self.participant_hex,
            team_name,
            proposal_id,
        )

    def wait_for_team_admission_signal(self, team_name: str, timeout: int = 15) -> bool:
        """Wait for a Hub-backed berth pulse that may imply fresh admission events."""
        key = (team_name, "encrypted")
        session = self._sessions.get(key)
        if session is None:
            return False
        try:
            known = {
                peer["teammate_id"]: int(peer.get("signal_count", 0))
                for peer in session.session_peers()
            }
            result = session.watch_notifications(known, timeout=timeout)
        except SmallSeaHubUnavailable:
            return False
        except Exception:
            _LOG.exception("Admission-event watch failed for team %s", team_name)
            return False
        return "updated" in result

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
