import contextlib
import logging
import pathlib
from dataclasses import dataclass
from typing import Literal, Optional

from cod_sync.repo import (
    Repo as _Repo,
    RepoError as _RepoError,
    RefDivergedError as _RefDivergedError,
)
from cod_sync.format import (
    LinkFormatError as _LinkFormatError,
    UnsupportedLinkVersionError as _UnsupportedLinkVersionError,
)
from cod_sync.protocol import (
    PARKED_REF_PREFIX as _PARKED_REF_PREFIX,
    ChainError as _ChainError,
    CodSync,
    NoPublishedHeadError as _NoPublishedHeadError,
    PinIntegrationRequiredError as _PinIntegrationRequiredError,
)
from cod_sync.store import (
    BootstrapProxyStore,
    CandidateInspectionStore,
    PeerSenderKeyUnavailableError as _StorePeerSenderKeyUnavailableError,
    PeerSmallSeaStore,
    PeerStorageUnknownError as _StorePeerStorageUnknownError,
    PublicationPendingError as _StorePublicationPendingError,
    SmallSeaStore,
    StoreError as _StoreError,
)
from small_sea_client.client import (
    SmallSeaClient,
    SmallSeaCloudStorageRequired,
    SmallSeaError,
    SmallSeaHubUnavailable,
)
from small_sea_manager import admission_events
from small_sea_manager import berth_source_decision
from small_sea_manager import note_to_self_sync
from small_sea_manager import provisioning
from small_sea_note_to_self.db import attached_note_to_self_connection

_CORE_APP = "SmallSeaCollectiveCore"
_LOG = logging.getLogger(__name__)

#: Hub `cloud_storage_required` reasons, mapped to the route reasons the
#: Manager speaks. Every one of them is retryable, but they do not share a
#: repair, so the two storage preconditions stay distinct: a missing location
#: is created by reconciling the route, while missing credentials have to be
#: supplied on the account itself. Neither is `storage_not_configured`, which
#: means no local storage account is registered at all.
ROUTE_REASON_BY_CLOUD_REASON = {
    "cloud_location_missing": "location_missing",
    "cloud_credentials_missing": "credentials_missing",
    "cloud_user_action_required": "user_action_required",
    "cloud_materialization_failed": "materialization_failed",
    "cloud_allocation_conflict": "allocation_conflict",
    # Neither is retryable. Both say the berth's placement is an open question
    # only a person can close, so they stay distinct from the preconditions
    # above rather than collapsing into `location_missing`.
    "berth_source_paused": "berth_source_paused",
    "berth_source_ambiguous": "berth_source_ambiguous",
}


#: Namespace for the durable device-local record of what was fetched from a
#: teammate's Core chain. Refs are the only truth here: no sidecar database,
#: watermark, or status column mirrors them.
CORE_PEER_REF_PREFIX = "refs/small-sea/core-peer"


def core_peer_latest_ref(teammate_id_hex: str) -> str:
    """The forward-only convenience ref for one teammate's Core chain.

    Named after the teammate, not a storage route: the chain belongs to the
    teammate and survives any replacement of the route it was read through.
    """
    return f"{CORE_PEER_REF_PREFIX}/{teammate_id_hex}/latest"


def core_peer_observation_ref(teammate_id_hex: str, link_uid: str) -> str:
    """The immutable ref recording one divergent observation of a teammate."""
    return f"{CORE_PEER_REF_PREFIX}/{teammate_id_hex}/observations/{link_uid}"


#: Namespace for what this device has fetched from a named placement
#: candidate of its own berth. Separate from the Core peer refs: a candidate is
#: a location of this participant's own berth, not a teammate's chain.
BERTH_SOURCE_REF_PREFIX = "refs/small-sea/berth-source"


def berth_source_candidate_ref(candidate_key: str) -> str:
    """The forward-only convenience ref for one inspected candidate."""
    return f"{BERTH_SOURCE_REF_PREFIX}/{candidate_key}/latest"


def berth_source_observation_ref(candidate_key: str, link_uid: str) -> str:
    """The immutable ref recording one verified observation of a candidate.

    Keyed by candidate as well as link, so two locations that publish the same
    head stay two observations rather than one.
    """
    return f"{BERTH_SOURCE_REF_PREFIX}/{candidate_key}/observations/{link_uid}"


class CoreFetchError(Exception):
    """Base class for every failure of a teammate Core fetch."""


class TeammateNotFoundError(CoreFetchError):
    """This team's Core DB holds no such teammate."""


class CorePublicationMissingError(CoreFetchError):
    """The teammate's store publishes no Core chain yet."""


class PeerSenderKeyUnavailableError(CoreFetchError):
    """This device cannot yet decrypt the teammate's published bytes."""


class PeerStorageUnknownError(CoreFetchError):
    """The Hub resolved no storage route for the teammate."""


class CorePublicationPendingError(CoreFetchError):
    """Local publication evidence needs resolution before this fetch can proceed."""

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        super().__init__(detail)


class CoreFetchRemoteError(CoreFetchError):
    """The Hub or the cloud provider behind it failed the read."""


class InvalidCoreChainError(CoreFetchError):
    """The fetched bytes are not a structurally valid Cod Sync chain."""


class CoreRefPersistenceError(CoreFetchError):
    """The local Git repository could not durably preserve the fetched source."""


@dataclass(frozen=True)
class TeammateCoreFetchResult:
    """What one explicit fetch of one teammate's Core chain observed.

    disposition is the convenience ref's outcome — "created", "advanced",
    "unchanged", or "stale" — or "divergent", where the ref was left alone and
    observation_ref_name durably names observed_head_sha instead.
    current_head_sha always names whatever latest_ref_name now holds.
    """

    disposition: str
    observed_head_sha: str
    current_head_sha: str
    latest_ref_name: str
    observation_ref_name: Optional[str] = None


@dataclass(frozen=True)
class CoreSourceHead:
    """One live parked head, derived from refs and ancestry at read time.

    Nothing here is stored state. Supersession is judged only within one
    logical source — one teammate, or this participant's own publications —
    so a head contained in a different teammate's history stays visible.
    contained_in_main says the local checkout already holds these commits; it
    does not say the state was integrated, and no field claims the fetched
    history is authored, admissible, or safe to adopt.
    """

    source_kind: Literal["teammate", "self_publication"]
    teammate_id: Optional[str]
    ref_name: str
    head_sha: str
    is_maximal: bool
    superseded_by_refs: tuple[str, ...]
    contained_in_main: bool


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
    outcome = note_to_self_sync.adopt_fetched_source(
        root_dir,
        bundle.participant_hex,
        repo,
        result.observed_head,
        result.link_uid,
    )
    if outcome.outcome != "integrated":
        raise ValueError(outcome.detail or "the fetched NoteToSelf source was refused")
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

    def _note_to_self_repo(self) -> _Repo:
        repo_dir = self._note_to_self_repo_dir()
        return _Repo(repo_dir / ".git", repo_dir)

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

        Commits any outstanding changes to core.db before pushing so that
        NoteToSelf mutations (e.g. new team rows from create_team) are included
        in the push without requiring callers to commit explicitly.

        "Outstanding" is a row difference, not a byte difference: adoption
        applies rows to the live database instead of checking out a blob, so a
        logically clean database can differ from HEAD byte-wise, and committing
        that would publish a head holding no change. The comparison and the
        commit run under one writer reservation, which is released before any
        Hub I/O, so the committed bytes are one stable SQLite state and no Hub
        write can land inside Git's read of the file.
        """
        session = self._open_note_to_self_session(mode="passthrough")
        berth_id, adopted = self._ensure_note_to_self_adopted_count(session)
        session.ensure_cloud_ready()
        repo_dir = self._note_to_self_repo_dir()
        nts_repo = _Repo(repo_dir / ".git", repo_dir)
        with note_to_self_sync.write_reservation(
            self.root_dir, self.participant_hex
        ) as conn:
            if note_to_self_sync.live_differs_from_head(nts_repo, conn):
                nts_repo.commit_paths(["core.db"], "Update NoteToSelf")
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
        """Fetch and adopt shared NoteToSelf updates through the Hub transport.

        Adoption runs on the same path as explicit integration, so no refresh
        can leave a conflicted core.db in the work tree, and a refusal reports
        itself instead of stranding a half-merged database.

        The fetched head is preserved under its immutable parked ref before the
        live database is touched. Dying before that ref exists has changed
        nothing and a later refresh refetches; once it exists, recovery is
        entirely local.
        """
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
        outcome = note_to_self_sync.adopt_fetched_source(
            self.root_dir,
            self.participant_hex,
            repo,
            result.observed_head,
            result.link_uid,
        )
        if outcome.outcome in ("integrated", "already_contained"):
            # Only state this device actually incorporated moves the baseline.
            provisioning.set_note_to_self_adopted_signal_count(
                self.root_dir,
                self.participant_hex,
                berth_id,
                pre_fetch_count,
            )
        return {
            "berth_id": berth_id.hex(),
            "adopted_count": provisioning.get_note_to_self_adopted_signal_count(
                self.root_dir, self.participant_hex, berth_id
            ),
            "integration": outcome,
            "teams": self.list_known_teams(),
        }

    def note_to_self_conflict_status(self) -> list:
        """Every outstanding stored NoteToSelf head, read from refs only.

        Makes no Hub contact, so a freshly started Manager can offer the
        integration without a session and without remembering anything.
        """
        return note_to_self_sync.outstanding_sources(self._note_to_self_repo())

    def integrate_note_to_self(self):
        """Combine outstanding stored NoteToSelf heads into local state."""
        return note_to_self_sync.integrate(
            self.root_dir, self.participant_hex, self._note_to_self_repo()
        )

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

    def connect_cloud_storage_credentials(
        self, storage_id_hex, access_key=None, secret_key=None,
        client_secret=None, refresh_token=None, access_token=None,
        token_expiry=None,
    ):
        """Save this device's credentials for an already registered account.

        Device-local only: the account, its berth allocations, and its
        announcements are unchanged, and nothing is published. The supplied
        credentials replace any existing ones completely.
        """
        provisioning.connect_cloud_storage_credentials(
            self.root_dir, self.participant_hex, storage_id_hex,
            access_key=access_key, secret_key=secret_key,
            client_secret=client_secret, refresh_token=refresh_token,
            access_token=access_token, token_expiry=token_expiry,
        )

    def disconnect_cloud_storage_credentials(self, storage_id_hex):
        """Delete this device's credentials for a registered account.

        The participant's account stays registered and allocated; only this
        device stops being able to reach it.
        """
        provisioning.disconnect_cloud_storage_credentials(
            self.root_dir, self.participant_hex, storage_id_hex
        )

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
        """Accept an invitation token (acceptor side). Returns a join-state report.

        Opens a NoteToSelf Hub session to proxy the inviter's cloud for cloning,
        then attempts route preparation. Joining is a two-phase local ceremony:
        all the local work lands here, and the route may stay pending until the
        Hub and the provider are available. Call prepare_team_route() to retry
        and export_admission_acceptance() to get the courier token once the
        route is ready.
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

        # Clone + local DB writes. No cloud push and no route publication
        # happen here; the low-level token return is deliberately discarded so
        # only the export gate can produce a courier token.
        provisioning.accept_invitation(
            self.root_dir, self.participant_hex, token_b64,
            inviter_store=proxy_store,
        )
        return self.prepare_team_route(token["team_name"])

    # ------------------------------------------------------------------ #
    # Route preparation and acceptance export
    # ------------------------------------------------------------------ #

    def _route_report(self, state, *, route_reason) -> dict:
        """Build the route-only report: what happened to the storage route.

        The established-teammate operation uses this directly. The invitation
        path extends it with join, admission, and acceptance fields.
        """
        route = state["route"]
        return {
            "route": route,
            "route_reason": None if route == "ready" else route_reason,
        }

    def _report(self, state, *, route_reason) -> dict:
        """Build the join-state report over derived state.

        Route and acceptance reasons stay independent: a route may be ready
        while the local acceptance artifact is absent or stale, and an artifact
        may be valid while provider setup is still pending.
        """
        _artifact, artifact_reason = provisioning.eligible_acceptance_artifact(
            self.root_dir, self.participant_hex, state
        )
        route = state["route"]
        if artifact_reason is not None:
            acceptance_reason = artifact_reason
        elif route != "ready":
            acceptance_reason = "route_pending"
        else:
            acceptance_reason = None
        return {
            "join": "complete" if state["join"] == "complete" else "absent",
            "admission": state["admission"],
            **self._route_report(state, route_reason=route_reason),
            "acceptance": "withheld" if acceptance_reason else "exportable",
            "acceptance_reason": acceptance_reason,
        }

    def _commit_prepared_route(self, team_name) -> bool:
        """Commit a ready route. False means the caller should report a retry."""
        try:
            self._team_repo(team_name).commit_paths(
                ["core.db"], "Announce berth storage"
            )
        except Exception:
            _LOG.exception("Committing a team storage route failed")
            return False
        return True

    @staticmethod
    def _pending_route(state) -> dict:
        pending_state = dict(state)
        pending_state["route"] = "pending"
        return pending_state

    @staticmethod
    def _same_allocation(expected, observed) -> bool:
        """Is `observed` the allocation generation and account `expected` names?

        Location is deliberately excluded: a provider-issued locator writeback
        changes it within one generation, and that is the value we came back
        to reread.
        """
        return (
            observed is not None
            and observed["id"] == expected["id"]
            and observed["cloud_storage_id"] == expected["cloud_storage_id"]
        )

    def _publish_core_route(
        self, team_name, state, allocation
    ) -> tuple[dict, str | None]:
        """Session, materialize, reread, publish, read back, commit.

        The shared spine of both route paths. It consumes an already-resolved
        allocation -- choosing one is the caller's policy -- and reads no
        acceptance artifact. `allocation` is also the expected generation: only
        a reread of that same allocation and account may be signed, so a
        replacement that landed mid-flight is reported rather than announced.

        Returns `(state, route_reason)`; a `None` reason means ready.
        """
        berth_id = state["core_berth_id"]
        try:
            session = self._get_or_open_session(team_name)
        except (SmallSeaHubUnavailable, SmallSeaError):
            return state, "hub_session_unavailable"

        try:
            session.ensure_cloud_ready()
        except SmallSeaCloudStorageRequired as exc:
            return state, ROUTE_REASON_BY_CLOUD_REASON.get(
                exc.reason, "route_preparation_error"
            )
        except SmallSeaHubUnavailable:
            return state, "hub_session_unavailable"
        except Exception:
            _LOG.exception("Cloud setup failed while preparing a team route")
            return state, "route_preparation_error"

        try:
            # Reread after materialization: the provider may have written back
            # a final locator, and only that one may be signed.
            reread = provisioning.get_berth_cloud_allocation_for_berth(
                self.root_dir, self.participant_hex, berth_id
            )
            if reread is None:
                return state, "storage_not_configured"
            if not self._same_allocation(allocation, reread):
                return state, "allocation_conflict"
            published = provisioning.publish_teammate_berth_storage_announcement(
                self.root_dir,
                self.participant_hex,
                team_name,
                state["self_in_team"],
                berth_id,
                reread,
            )
            announcement = provisioning.read_teammate_berth_storage_announcement(
                self.root_dir,
                self.participant_hex,
                team_name,
                bytes.fromhex(published["announcement_id_hex"]),
            )
        except Exception:
            _LOG.exception("Publishing a team storage route failed")
            return state, "route_preparation_error"

        if announcement is None or announcement.signer_key_id != state["device_key_id"]:
            # The selected row may be an older one signed by another device.
            # Attaching it would reach the inviter and fail their signer check.
            _LOG.error(
                "Read-back storage announcement is missing or signed by another device"
            )
            return state, "route_preparation_error"

        if not self._commit_prepared_route(team_name):
            return self._pending_route(state), "route_preparation_error"

        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        if not self._same_allocation(allocation, state["allocation"]):
            # A replacement became visible after publication. This route is
            # published, but it is no longer the allocation in force.
            return self._pending_route(state), "allocation_conflict"
        return state, None

    def prepare_team_route(self, team_name) -> dict:
        """Publish this participant's Core storage route, and report join state.

        Retryable: every failure below leaves the completed local join intact
        and names a reason the caller can act on and try again. Provider I/O is
        skipped entirely unless this exact join has an eligible acceptance
        artifact to export once the route lands.
        """
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        if state["join"] != "complete":
            raise ValueError(f"Team '{team_name}' has no local join to prepare")

        artifact, _artifact_reason = provisioning.eligible_acceptance_artifact(
            self.root_dir, self.participant_hex, state
        )
        if artifact is None:
            # An acceptance-state failure, not a route failure: nothing to
            # export after success, so no provider is contacted.
            return self._report(state, route_reason=None)

        if state["route"] == "ready":
            if not self._commit_prepared_route(team_name):
                return self._report(
                    self._pending_route(state), route_reason="route_preparation_error"
                )
            return self._report(state, route_reason=None)

        if state["core_berth_id"] is None:
            return self._report(state, route_reason="storage_not_configured")

        # Allocate rather than only reading what acceptance left behind, so an
        # invitee who adds cloud storage afterwards retries into a route. The
        # silent first-account pick belongs to this path alone: an invitee is
        # racing to publish a first route, not choosing where their data lives.
        allocation = provisioning._auto_allocate_berth_cloud_if_available(
            self.root_dir, self.participant_hex, state["core_berth_id"]
        )
        if allocation is None:
            return self._report(state, route_reason="storage_not_configured")

        state, route_reason = self._publish_core_route(team_name, state, allocation)
        return self._report(state, route_reason=route_reason)

    def reconcile_team_route(
        self, team_name, cloud_storage_id=None, new_location=False
    ) -> dict:
        """Reconcile and publish an established teammate's Core storage route.

        Repairs a missing or unusable route, and carries out an intentional
        provider, account, or location change. No acceptance artifact is
        involved: this is the operation for a teammate who is already admitted.

        A change request is one-shot. Once the replacement allocation is
        durable, retry with no change arguments; repeating `new_location` asks
        for another rotation. Between replacement and successful publication
        this device's own Hub cloud reads and writes for the team fail with
        `announcement_missing` until a retry converges.

        `cloud_storage_id` names a registered account; a malformed or
        unregistered one raises `ValueError` before anything is mutated. There
        is deliberately no raw location parameter.
        """
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        if state["join"] != "complete":
            raise ValueError(f"Team '{team_name}' has no local join to reconcile")

        if cloud_storage_id is not None:
            provisioning.validate_cloud_storage_id(
                self.root_dir, self.participant_hex, cloud_storage_id
            )

        if state["admission"] != "finalized":
            # Peers skip announcements from keys they no longer trust, so a row
            # signed by this key could not repair their selection anyway. A
            # locally valid announcement is not a usable route here, so this
            # reports pending however the local state derives.
            return self._route_report(
                self._pending_route(state), route_reason="current_device_untrusted"
            )

        if state["core_berth_id"] is None:
            return self._route_report(state, route_reason="storage_not_configured")

        allocation, reason = provisioning.resolve_berth_cloud_allocation_intent(
            self.root_dir,
            self.participant_hex,
            state["core_berth_id"],
            cloud_storage_id_hex=cloud_storage_id,
            new_location=new_location,
        )
        if allocation is None:
            return self._route_report(state, route_reason=reason)

        # Re-derived after the intent, so an already-ready old route cannot
        # suppress a requested provider or location change.
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        if not self._same_allocation(allocation, state["allocation"]):
            return self._route_report(
                self._pending_route(state), route_reason="allocation_conflict"
            )
        if state["route"] == "ready":
            if not self._commit_prepared_route(team_name):
                return self._route_report(
                    self._pending_route(state), route_reason="route_preparation_error"
                )
            return self._route_report(state, route_reason=None)

        state, route_reason = self._publish_core_route(team_name, state, allocation)
        return self._route_report(state, route_reason=route_reason)

    def core_storage_allocation(self, team_name) -> dict:
        """Report this device's current Core storage decision for one team.

        Read-only and local: it names the account and location this device
        would publish, not whether peers can reach it.

        `placement` is "settled", "paused" or "ambiguous". It is here so an
        existing caller notices an open placement question without having to
        learn `berth_source_status`; a paused or ambiguous berth reports no
        allocation, because there is none this device chose.
        """
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        return {
            "allocation": state["allocation"],
            "route": state["route"],
            "admission": state["admission"],
            "placement": state["placement"],
        }

    # ------------------------------------------------------------------ #
    # A berth's source-use question: report it, investigate it, decide it
    # ------------------------------------------------------------------ #

    def berth_source_status(self, team_name) -> dict:
        """Report the placement question this device holds for a team's Core berth.

        Refreshes device-local evidence and makes no provider request, so it
        can be read while every ordinary operation on the berth is refused.
        Refreshing can change the report -- a candidate a sibling deleted is
        retained here, and a newly arrived competing row opens a pause -- so
        this is not read-only, and the digest it returns is what a decision
        made now would be checked against.
        """
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        berth_id = state["core_berth_id"]
        if berth_id is None:
            return {
                "berth_id": None,
                "paused": False,
                "placement": state["placement"],
                "candidates": [],
                "blocked": [],
            }
        repo = self._note_to_self_repo()
        refreshed = berth_source_decision.refresh_report(
            self.root_dir,
            self.participant_hex,
            berth_id,
            retained_sources_fn=lambda: note_to_self_sync.stored_head_berth_routes(
                repo, berth_id
            ),
            retained_observations_fn=lambda: self._stored_berth_source_observations(team_name),
        )
        report = refreshed["report"]
        pause = refreshed["pause"]
        choice = refreshed["choice"]
        live = [c for c in report["candidates"] if c["live"]]

        blocked = []
        if pause is not None:
            # Every own-berth provider operation resolves through the Hub's one
            # allocation lookup, so the pause covers uploads, downloads,
            # materialization, runtime artifacts and signals for this berth.
            blocked.append("berth_source_paused")
        elif len(live) > 1:
            blocked.append("berth_source_ambiguous")
        outstanding = note_to_self_sync.outstanding_sources(repo)
        if outstanding:
            # Channel-wide, not berth-scoped: a source that has not been
            # adopted holds up every row travelling with it.
            blocked.append("note_to_self_sources_outstanding")

        return {
            "berth_id": berth_id.hex(),
            "paused": pause is not None,
            "detected_at": pause["detected_at"] if pause is not None else None,
            "placement": "paused"
            if pause is not None
            else ("ambiguous" if len(live) > 1 else "settled"),
            "evidence_digest": refreshed["evidence_digest"].hex(),
            "candidates": report["candidates"],
            "unavailable": report["unavailable"],
            "blocked": blocked,
            "outstanding_note_to_self_sources": [s.ref_name for s in outstanding],
            "decided": None
            if choice is None
            else {
                "allocation_id": choice["allocation_id"],
                "decided_at": choice["decided_at"],
                "evidence_digest": choice["evidence_digest"].hex(),
            },
        }

    def inspect_berth_source_candidate(self, team_name, candidate_key) -> dict:
        """Fetch one retained candidate's Core chain without integrating it.

        The whole walk is bound to the named candidate: every object request
        carries the same key, and the Hub refuses account routing that differs
        from the saved snapshot. A newer announcement cannot switch sources.

        Objects are imported and the observed head is preserved under a ref of
        this candidate's own; `main` never moves and nothing is integrated. The
        outcome is published under the reservation used by resolution, which
        invalidates an earlier review. If resolution finished before the fetch,
        the later evidence stays inspectable without rewriting that decision.

        Whether a trusted device of this participant announced the candidate's
        route is recorded alongside the outcome rather than gating the read.
        The sibling's announcement usually travels in a team chain published to
        the very location this device cannot reach, so requiring it would make
        the disputed location unreadable exactly when someone needs to look.
        Investigation never writes, so nothing depends on the route being one
        teammates would look at.
        """
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        berth_id = state["core_berth_id"]
        if berth_id is None:
            raise ValueError(f"Team '{team_name}' has no Core berth to inspect")

        repo = self._team_repo(team_name)
        observation = {
            "reached": True,
            "announcement": self._candidate_announcement_status(
                team_name, state, berth_id, candidate_key
            ),
        }
        result = None
        try:
            session = self._get_or_open_session(team_name)
            store = CandidateInspectionStore(
                session.token,
                candidate_key,
                base_url=self.client._base_url,
                client=self.client._http_client,
            )
            # Import and verify outside the writer reservation. Publishing a
            # ref makes this evidence visible and belongs with the report.
            result = CodSync(repo, store).fetch()
        except _NoPublishedHeadError as exc:
            observation.update(disposition="empty", detail=str(exc))
        except (
            _ChainError,
            _LinkFormatError,
            _UnsupportedLinkVersionError,
            _StoreError,
            SmallSeaHubUnavailable,
            SmallSeaError,
            _RepoError,
        ) as exc:
            # Recorded rather than raised: a candidate that cannot be read is
            # missing evidence a person may still decide without, and saying
            # so is more useful than failing the investigation.
            observation.update(
                reached=False, failure=type(exc).__name__, detail=str(exc)
            )
        retained = berth_source_decision.record_observation(
            self.root_dir, self.participant_hex, berth_id, candidate_key, observation,
            publish_observation_fn=(
                lambda: self._publish_berth_source_observation(repo, candidate_key, result)
            ) if result is not None else None,
        )
        return {
            "candidate_key": candidate_key,
            "observation": observation,
            "retained": retained,
        }

    @staticmethod
    def _publish_berth_source_observation(repo, candidate_key, result) -> dict:
        """Publish verified evidence while the NoteToSelf writer lock is held."""
        ref_name = berth_source_observation_ref(candidate_key, result.link_uid)
        # Preserve every observation before moving the convenience ref, so a
        # later fetch cannot erase evidence left by an interrupted report write.
        repo.create_ref_immutable(ref_name, result.observed_head)
        try:
            advance = repo.advance_ref(
                berth_source_candidate_ref(candidate_key), result.observed_head
            )
            disposition = advance.disposition
        except _RefDivergedError:
            disposition = "divergent"
        return {
            "disposition": disposition,
            "observed_head": result.observed_head,
            "ref_name": ref_name,
        }

    def _stored_berth_source_observations(self, team_name) -> list:
        """Read source-associated heads under the caller's writer reservation."""
        observations = []
        for ref_name, head in sorted(
            self._team_repo(team_name).list_refs(BERTH_SOURCE_REF_PREFIX).items()
        ):
            parts = ref_name[len(BERTH_SOURCE_REF_PREFIX) + 1:].split("/")
            if (len(parts) == 2 and parts[1] == "latest") or (
                len(parts) == 3 and parts[1] == "observations"
            ):
                observations.append((parts[0], ref_name, head))
        # Recover an immutable ref in preference to a convenience ref naming
        # the same head; the latter may advance on the next inspection.
        return sorted(observations, key=lambda entry: entry[1].endswith("/latest"))

    def _candidate_announcement_status(
        self, team_name, state, berth_id, candidate_key
    ) -> str:
        """Announcement evidence for one candidate, or why there is none."""
        with contextlib.closing(
            attached_note_to_self_connection(self.root_dir, self.participant_hex)
        ) as conn:
            route = berth_source_decision.saved_route_for_candidate(
                conn, berth_id, candidate_key
            )
        if route is None:
            return "unknown"
        return provisioning.candidate_announcement_status(
            self.root_dir, self.participant_hex, team_name, state, route
        )

    def resolve_berth_source(self, team_name, candidate_key, evidence_digest) -> dict:
        """Apply the human's choice of which location this Core berth keeps.

        `evidence_digest` is the hex digest from the status report the person
        actually read. If the question moved since -- a new candidate, a
        locator writeback, another observation -- the choice is refused and the
        updated report comes back instead of a silent decision over stale
        evidence.

        Resolution does not by itself resume publication: this device's own
        signed announcement may still name the location it gave up, so an
        argument-free `reconcile_team_route` is the second half of choosing a
        sibling's location.
        """
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        berth_id = state["core_berth_id"]
        if berth_id is None:
            raise ValueError(f"Team '{team_name}' has no Core berth to resolve")
        repo = self._note_to_self_repo()
        try:
            berth_source_decision.resolve_berth_source(
                self.root_dir,
                self.participant_hex,
                berth_id,
                candidate_key,
                bytes.fromhex(evidence_digest),
                retained_sources_fn=lambda: note_to_self_sync.stored_head_berth_routes(
                    repo, berth_id
                ),
                retained_observations_fn=lambda: self._stored_berth_source_observations(team_name),
            )
        except berth_source_decision.EvidenceChangedError:
            status = self.berth_source_status(team_name)
            return {"resolved": False, "reason": "evidence_changed", "status": status}
        except berth_source_decision.CandidateNotRestorableError as exc:
            status = self.berth_source_status(team_name)
            return {
                "resolved": False,
                "reason": "candidate_not_restorable",
                "detail": str(exc),
                "status": status,
            }
        return {"resolved": True, "status": self.berth_source_status(team_name)}

    def export_admission_acceptance(self, team_name) -> dict:
        """Return the courier token for a prepared join, or say why it is withheld.

        The gate has no exception: an acceptance with no route is never handed
        out, so the invitee never spends the proposal on a token the inviter
        cannot route back to.
        """
        state = provisioning.derive_team_join_state(
            self.root_dir, self.participant_hex, team_name
        )
        report = self._report(state, route_reason=None)
        report["acceptance_token"] = None
        if report["acceptance"] != "exportable":
            return report

        if not self._commit_prepared_route(team_name):
            failure = self._report(
                self._pending_route(state), route_reason="route_preparation_error"
            )
            failure["acceptance_token"] = None
            return failure

        artifact, _reason = provisioning.eligible_acceptance_artifact(
            self.root_dir, self.participant_hex, state
        )
        announcement = provisioning.selected_own_berth_storage_announcement(
            self.root_dir,
            self.participant_hex,
            team_name,
            state,
        )
        acceptance_token = provisioning.export_admission_acceptance(
            self.root_dir, self.participant_hex, artifact
        )
        report["acceptance_token"] = provisioning.build_acceptance_courier_token(
            acceptance_token,
            provisioning.serialize_berth_storage_announcement(announcement),
        )
        return report

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

    # ------------------------------------------------------------------ #
    # Incoming Core state: fetch and park, never integrate
    # ------------------------------------------------------------------ #

    def fetch_teammate_core(self, team_name, teammate_id) -> TeammateCoreFetchResult:
        """Fetch one teammate's Core chain and preserve the head it published.

        Explicit and unconditional: no notification state is read, and nothing
        about the local repository decides whether the fetch happens. Objects
        are imported without a checkout, local `main` never moves, and the only
        refs written are this teammate's own.

        A head that diverges from what was fetched before is an observation,
        not a failure. The convenience ref keeps its head and the newly seen
        one is recorded under its own immutable ref, so a later fetch of the
        same link finds that record rather than wedging.

        The result says what this device fetched from the store the Hub
        selected. It is not evidence that the teammate authored the
        publication, that the chain may extend local history, or that any of
        it may be integrated.
        """
        # A well-formed id that names no teammate is worth distinguishing here,
        # before any peer transport. Route selection stays the Hub's: this
        # lookup reads no route data, so it cannot disagree with the Hub about
        # where the teammate's chain lives.
        if not provisioning.teammate_exists(
            self.root_dir, self.participant_hex, team_name, teammate_id
        ):
            raise TeammateNotFoundError(
                f"team {team_name!r} has no teammate {teammate_id}"
            )

        repo = self._team_repo(team_name)
        latest_ref = core_peer_latest_ref(teammate_id)
        try:
            session = self._get_or_open_session(team_name)
            store = PeerSmallSeaStore(
                session.token,
                teammate_id,
                base_url=self.client._base_url,
                client=self.client._http_client,
            )
            result = CodSync(repo, store).fetch(pin_to_ref=latest_ref)
        except _PinIntegrationRequiredError as exc:
            return self._record_core_divergence(repo, teammate_id, latest_ref, exc)
        except _NoPublishedHeadError as exc:
            raise CorePublicationMissingError(
                f"teammate {teammate_id} publishes no Core chain"
            ) from exc
        except (
            _ChainError,
            _LinkFormatError,
            _UnsupportedLinkVersionError,
        ) as exc:
            # Cod Sync raises its link-decoding errors outside CodSyncError, so
            # they are named here rather than reached through a base class.
            raise InvalidCoreChainError(str(exc)) from exc
        except _StorePeerStorageUnknownError as exc:
            raise PeerStorageUnknownError(str(exc)) from exc
        except _StorePeerSenderKeyUnavailableError as exc:
            raise PeerSenderKeyUnavailableError(str(exc)) from exc
        except _StorePublicationPendingError as exc:
            raise CorePublicationPendingError(exc.reason, str(exc)) from exc
        except _StoreError as exc:
            raise CoreFetchRemoteError(str(exc)) from exc
        except (SmallSeaHubUnavailable, SmallSeaError) as exc:
            raise CoreFetchRemoteError(str(exc)) from exc
        except _RepoError as exc:
            # Cod Sync converts failures while inspecting or importing fetched
            # bundle bytes into ChainError. What remains here is ref contention
            # or a hard ref-write failure, neither of which leaves a newly
            # fetched source durably claimed.
            raise CoreRefPersistenceError(str(exc)) from exc

        return TeammateCoreFetchResult(
            disposition=result.pin_disposition,
            observed_head_sha=result.observed_head,
            current_head_sha=result.pinned_head,
            latest_ref_name=latest_ref,
        )

    def _record_core_divergence(
        self, repo, teammate_id, latest_ref, exc
    ) -> TeammateCoreFetchResult:
        """Preserve a divergent teammate head under its own immutable ref.

        The objects are already imported by the time the pin refuses to move,
        so the only thing at risk is the record of which head was seen. One ref
        per observed link means a repeated fetch of the same link verifies the
        record it already wrote instead of failing.
        """
        observation_ref = core_peer_observation_ref(teammate_id, exc.link_uid)
        try:
            repo.create_ref_immutable(observation_ref, exc.observed_head)
        except _RepoError as err:
            raise CoreRefPersistenceError(
                f"could not preserve {observation_ref} at {exc.observed_head}: {err}"
            ) from err
        return TeammateCoreFetchResult(
            disposition="divergent",
            observed_head_sha=exc.observed_head,
            current_head_sha=exc.current_sha,
            latest_ref_name=latest_ref,
            observation_ref_name=observation_ref,
        )

    @staticmethod
    def _core_peer_teammate_id(ref_name: str) -> Optional[str]:
        """Return the teammate a Core peer ref belongs to, or None if unreadable."""
        remainder = ref_name[len(CORE_PEER_REF_PREFIX) + 1 :]
        parts = remainder.split("/")
        if len(parts) == 2 and parts[1] == "latest":
            return parts[0]
        if len(parts) == 3 and parts[1] == "observations":
            return parts[0]
        return None

    def list_core_source_heads(self, team_name) -> list[CoreSourceHead]:
        """Report every parked Core head this device currently holds.

        Derived entirely from live refs and Git ancestry, so a fresh
        TeamManager sees exactly what the previous one left on disk. Teammate
        sources come from this operation's own refs; self-publication sources
        are the refs Cod Sync's publication settlement already parked, read
        here and otherwise left alone.

        Supersession is decided within one logical source only, and only by
        strict descent: two refs at the same SHA both stay maximal, and
        ordinary divergence keeps both heads rather than picking a winner.
        """
        repo = self._team_repo(team_name)
        main_sha = repo.resolve_ref("refs/heads/main")

        entries = []
        for ref_name, sha in repo.list_refs(CORE_PEER_REF_PREFIX).items():
            teammate_id = self._core_peer_teammate_id(ref_name)
            if teammate_id is None:
                _LOG.warning("ignoring unrecognized Core peer ref %s", ref_name)
                continue
            entries.append((("teammate", teammate_id), "teammate", teammate_id, ref_name, sha))
        for ref_name, sha in repo.list_refs(_PARKED_REF_PREFIX).items():
            # Cod Sync parks a competing head this participant's own
            # publication lost to. Which device wrote it is not recorded, so
            # the source is not attributed to one.
            entries.append((("self_publication", None), "self_publication", None, ref_name, sha))

        heads = []
        for source, kind, teammate_id, ref_name, sha in entries:
            superseded_by = tuple(sorted(
                other_ref
                for other_source, _kind, _id, other_ref, other_sha in entries
                if other_source == source
                and other_sha != sha
                and repo.is_ancestor(sha, other_sha)
            ))
            heads.append(
                CoreSourceHead(
                    source_kind=kind,
                    teammate_id=teammate_id,
                    ref_name=ref_name,
                    head_sha=sha,
                    is_maximal=not superseded_by,
                    superseded_by_refs=superseded_by,
                    contained_in_main=(
                        main_sha is not None and repo.is_ancestor(sha, main_sha)
                    ),
                )
            )
        return sorted(heads, key=lambda head: head.ref_name)

    def complete_invitation_acceptance(self, team_name, acceptance_b64) -> dict:
        """Record invitee acceptance and finalize when quorum is met.

        Returns provisioning's route-delivery and admission report so callers
        can tell the inviter whether a route arrived with the acceptance.
        """
        return provisioning.complete_invitation_acceptance(
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
