"""Micro tests for device-local cloud credential connection (issue #237).

A device that inherits a participant's cloud account through NoteToSelf has no
credentials for it. These tests witness the Manager operations that supply and
withdraw those credentials on one device: they resolve an existing account by
ID, touch only the device-local credential row, and leave the shared account,
its berth allocation, and its announcements exactly as they were.

The read model is the other half. `credentials_on_this_device` reports stored
material and nothing more -- not that the provider would accept it, and not
that a shared `client_id` means this device is connected.
"""

import pathlib
import sqlite3

import pytest
import small_sea_hub.backend as SmallSea
import small_sea_manager.provisioning as provisioning
from fastapi.testclient import TestClient
from small_sea_client.client import SmallSeaCloudStorageRequired
from small_sea_hub.server import app as hub_app
from small_sea_manager.manager import (
    TeamManager,
    bootstrap_existing_identity,
    create_identity_join_request,
)
from small_sea_manager.web import create_app
from small_sea_note_to_self.db import (
    device_local_db_path,
    note_to_self_sync_db_path,
)

TEAM = "ProjectX"


# --------------------------------------------------------------------------- #
# Setup helpers
# --------------------------------------------------------------------------- #


def _alice(root):
    """A participant with a team and one registered S3 account."""
    alice_hex = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, alice_hex, TEAM)
    return alice_hex


def _register(root, participant_hex, *, protocol="s3", url="http://localhost:9000",
              **credentials):
    """Register an account, then make it look inherited by clearing its row.

    `add_cloud_storage` always writes a local credential row. A device that
    received the account through NoteToSelf instead has the shared row and no
    local one, which is the state these tests care about.
    """
    storage_id = provisioning.add_cloud_storage(
        root, participant_hex, protocol=protocol, url=url, **credentials
    )
    if not credentials:
        provisioning.disconnect_cloud_storage_credentials(
            root, participant_hex, storage_id
        )
    return storage_id


def _shared_state(root, participant_hex):
    """Every shared row this branch must not disturb."""
    with sqlite3.connect(note_to_self_sync_db_path(root, participant_hex)) as conn:
        return {
            "cloud_storage": conn.execute(
                "SELECT id, protocol, url, client_id, path_metadata"
                " FROM cloud_storage ORDER BY rowid"
            ).fetchall(),
            "berth_cloud_allocation": conn.execute(
                "SELECT id, berth_id, cloud_storage_id, location, created_at"
                " FROM berth_cloud_allocation ORDER BY rowid"
            ).fetchall(),
        }


def _credential_row(root, participant_hex, storage_id_hex):
    with sqlite3.connect(device_local_db_path(root, participant_hex)) as conn:
        return conn.execute(
            "SELECT access_key, secret_key, client_secret, refresh_token,"
            " access_token, token_expiry FROM cloud_storage_credential"
            " WHERE cloud_storage_id = ?",
            (bytes.fromhex(storage_id_hex),),
        ).fetchone()


def _by_id(providers, storage_id_hex):
    return next(p for p in providers if p["id"] == storage_id_hex)


def _web(root, participant_hex):
    return TestClient(create_app(str(root), participant_hex))


# --------------------------------------------------------------------------- #
# The Manager operations
# --------------------------------------------------------------------------- #


def test_connect_replace_and_disconnect_leave_shared_state_untouched(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    inherited = _register(root, alice_hex)
    other = _register(
        root, alice_hex, url="http://localhost:9001",
        access_key="other-key", secret_key="other-secret",
    )
    mgr = TeamManager(root, alice_hex)
    before = _shared_state(root, alice_hex)

    mgr.connect_cloud_storage_credentials(
        inherited, access_key="first-key", secret_key="first-secret"
    )
    assert _credential_row(root, alice_hex, inherited)[:2] == ("first-key", "first-secret")
    assert _shared_state(root, alice_hex) == before

    mgr.connect_cloud_storage_credentials(
        inherited, access_key="second-key", secret_key="second-secret"
    )
    assert _credential_row(root, alice_hex, inherited)[:2] == ("second-key", "second-secret")
    assert _shared_state(root, alice_hex) == before

    mgr.disconnect_cloud_storage_credentials(inherited)
    assert _credential_row(root, alice_hex, inherited) is None
    # Disconnecting an already disconnected account is harmless.
    mgr.disconnect_cloud_storage_credentials(inherited)
    assert _credential_row(root, alice_hex, inherited) is None
    assert _shared_state(root, alice_hex) == before

    # The sibling account never moved.
    assert _credential_row(root, alice_hex, other)[:2] == ("other-key", "other-secret")


def test_replacement_clears_credentials_that_were_not_resupplied(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    storage_id = _register(
        root, alice_hex, protocol="dropbox", url="https://api.dropboxapi.com",
        refresh_token="stale-refresh", access_token="stale-access",
        token_expiry="2026-01-01T00:00:00Z",
    )
    mgr = TeamManager(root, alice_hex)

    mgr.connect_cloud_storage_credentials(storage_id, client_secret="fresh-secret")

    assert _credential_row(root, alice_hex, storage_id) == (
        None, None, "fresh-secret", None, None, None,
    )


@pytest.mark.parametrize(
    "bad_id",
    ["not-hex", "abcd", "00" * 16],
    ids=["malformed", "odd-length-ish", "unregistered"],
)
def test_unresolvable_account_ids_fail_without_mutating_anything(playground_dir, bad_id):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    storage_id = _register(
        root, alice_hex, access_key="live-key", secret_key="live-secret"
    )
    mgr = TeamManager(root, alice_hex)
    before = _shared_state(root, alice_hex)

    with pytest.raises(ValueError):
        mgr.connect_cloud_storage_credentials(
            bad_id, access_key="new-key", secret_key="new-secret"
        )
    with pytest.raises(ValueError):
        mgr.disconnect_cloud_storage_credentials(bad_id)

    assert _credential_row(root, alice_hex, storage_id)[:2] == ("live-key", "live-secret")
    assert _shared_state(root, alice_hex) == before


def test_another_participants_account_is_not_reachable(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    bob_hex = provisioning.create_new_participant(root, "Bob")
    bob_storage = _register(root, bob_hex, url="http://localhost:9002")

    with pytest.raises(ValueError):
        TeamManager(root, alice_hex).connect_cloud_storage_credentials(
            bob_storage, access_key="k", secret_key="s"
        )
    assert _credential_row(root, bob_hex, bob_storage) is None


@pytest.mark.parametrize(
    "credentials",
    [{"access_key": "k"}, {"secret_key": "s"}, {}],
    ids=["key-only", "secret-only", "neither"],
)
def test_incomplete_s3_credentials_never_replace_a_working_row(playground_dir, credentials):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    storage_id = _register(
        root, alice_hex, access_key="live-key", secret_key="live-secret"
    )
    mgr = TeamManager(root, alice_hex)

    with pytest.raises(ValueError):
        mgr.connect_cloud_storage_credentials(storage_id, **credentials)

    assert _credential_row(root, alice_hex, storage_id)[:2] == ("live-key", "live-secret")


def test_saving_credentials_publishes_nothing(playground_dir, monkeypatch):
    """A local save is not a shared change and contacts no provider.

    The Manager methods that would publish or reach a provider are replaced
    with failures, so calling any of them is a test failure rather than a
    silent extra effect.
    """
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    storage_id = _register(root, alice_hex)
    mgr = TeamManager(root, alice_hex)

    def forbidden(*args, **kwargs):
        raise AssertionError("credential connection must not leave this device")

    for name in (
        "push_note_to_self",
        "push_team",
        "publish_teammate_berth_storage_announcement",
        "reconcile_team_route",
        "refresh_note_to_self",
    ):
        monkeypatch.setattr(TeamManager, name, forbidden)

    repo = pathlib.Path(root) / "Participants" / alice_hex / "NoteToSelf"
    head_before = (repo / "Sync" / ".git" / "HEAD").read_bytes() if repo.exists() else None
    commits_before = _note_to_self_commit_count(root, alice_hex)

    mgr.connect_cloud_storage_credentials(
        storage_id, access_key="k", secret_key="s"
    )
    mgr.disconnect_cloud_storage_credentials(storage_id)

    assert _note_to_self_commit_count(root, alice_hex) == commits_before
    if head_before is not None:
        assert (repo / "Sync" / ".git" / "HEAD").read_bytes() == head_before


def _note_to_self_commit_count(root, participant_hex):
    import subprocess

    repo = pathlib.Path(root) / "Participants" / participant_hex / "NoteToSelf" / "Sync"
    if not (repo / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


# --------------------------------------------------------------------------- #
# The read model
# --------------------------------------------------------------------------- #


def test_saved_material_is_reported_without_claiming_the_provider_works(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)

    inherited = _register(root, alice_hex, url="http://localhost:9000")
    complete = _register(
        root, alice_hex, url="http://localhost:9001",
        access_key="full-key", secret_key="full-secret",
    )
    partial = _register(
        root, alice_hex, url="http://localhost:9002", access_key="half-key"
    )
    expiry_only = _register(
        root, alice_hex, url="http://localhost:9003",
        token_expiry="2026-01-01T00:00:00Z",
    )
    oauth_shared_only = _register(
        root, alice_hex, protocol="gdrive", url="https://www.googleapis.com",
        client_id="app-client-id",
    )
    oauth_local = _register(
        root, alice_hex, protocol="dropbox", url="https://api.dropboxapi.com",
        client_id="app-client-id", refresh_token="refresh",
    )

    providers = TeamManager(root, alice_hex).list_cloud_storage()
    saved = {p["id"]: p["credentials_on_this_device"] for p in providers}

    assert saved[inherited] is False
    assert saved[complete] is True
    # Partial material still counts as saved: the Hub decides usability.
    assert saved[partial] is True
    # An expiry describes credentials; it is not any.
    assert saved[expiry_only] is False
    # A shared app identifier says nothing about this device.
    assert saved[oauth_shared_only] is False
    assert _by_id(providers, oauth_shared_only)["client_id"] == "app-client-id"
    assert saved[oauth_local] is True

    # No secret leaves the read model.
    rendered = repr(providers)
    for secret in ("full-secret", "refresh"):
        assert secret not in rendered


# --------------------------------------------------------------------------- #
# The web surface
# --------------------------------------------------------------------------- #


def test_web_connect_replace_and_disconnect_drive_the_manager_operation(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    storage_id = _register(root, alice_hex)
    client = _web(root, alice_hex)
    before = _shared_state(root, alice_hex)

    body = client.get("/cloud-storage").text
    assert "No credentials saved on this device" in body

    body = client.post(
        f"/cloud-storage/{storage_id}/connect",
        data={"access_key": "web-key", "secret_key": "web-secret"},
    ).text
    assert "Credentials saved on this device" in body
    assert "web-secret" not in body
    assert _credential_row(root, alice_hex, storage_id)[:2] == ("web-key", "web-secret")

    client.post(
        f"/cloud-storage/{storage_id}/connect",
        data={"access_key": "web-key-2", "secret_key": "web-secret-2"},
    )
    assert _credential_row(root, alice_hex, storage_id)[:2] == ("web-key-2", "web-secret-2")

    body = client.post(f"/cloud-storage/{storage_id}/disconnect").text
    assert "No credentials saved on this device" in body
    assert _credential_row(root, alice_hex, storage_id) is None
    assert _shared_state(root, alice_hex) == before


def test_web_reports_a_rejected_connection_without_echoing_the_secret(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    storage_id = _register(
        root, alice_hex, access_key="live-key", secret_key="live-secret"
    )
    client = _web(root, alice_hex)

    body = client.post(
        f"/cloud-storage/{storage_id}/connect",
        data={"access_key": "", "secret_key": "rejected-secret"},
    ).text
    assert "notice-err" in body
    assert "rejected-secret" not in body
    assert _credential_row(root, alice_hex, storage_id)[:2] == ("live-key", "live-secret")

    body = client.post(
        "/cloud-storage/not-hex/connect",
        data={"access_key": "k", "secret_key": "s"},
    ).text
    assert "notice-err" in body


def test_web_separates_device_connection_from_participant_wide_configuration(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    storage_id = _register(root, alice_hex)
    body = _web(root, alice_hex).get("/cloud-storage").text

    assert f"/cloud-storage/{storage_id}/connect" in body
    assert f"/cloud-storage/{storage_id}/remove" in body
    # Removal is a participant-wide change and says so; connection does not
    # pretend to be one, and neither is a side effect of the other.
    assert "Remove account" in body
    assert "participant" in body
    assert "Connect on this device" in body


def test_web_offers_no_s3_form_for_a_provider_manager_cannot_set_up(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _alice(root)
    gdrive = _register(
        root, alice_hex, protocol="gdrive", url="https://www.googleapis.com",
        client_id="app-client-id",
    )
    body = _web(root, alice_hex).get("/cloud-storage").text

    assert "Credential setup for this provider is not available in Manager yet" in body
    assert f"/cloud-storage/{gdrive}/connect" not in body
    # The shared app identifier must not read as a connected device.
    assert "No credentials saved on this device" in body


# --------------------------------------------------------------------------- #
# Two-device witness against local MinIO
# --------------------------------------------------------------------------- #


def _use(backend):
    """Bind the Hub app to one device's backend and hand back its client.

    The Hub app carries a single backend, so every request below must be made
    through the device it belongs to. Binding and building the client together
    is what keeps device B's request from being answered by device A's Hub.
    """
    hub_app.state.backend = backend
    return TestClient(hub_app)


def _open_note_to_self_session(http, nickname):
    resp = http.post(
        "/sessions/request",
        json={
            "participant": nickname,
            "app": "SmallSeaCollectiveCore",
            "team": "NoteToSelf",
            "client": "Credential witness",
            "mode": "passthrough",
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    if "token" in result:
        return result["token"]
    resp = http.post(
        "/sessions/confirm",
        json={"pending_id": result["pending_id"], "pin": result["pin"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _unchanged_by(root, participant_hex, operation):
    """Run a credential operation and assert it moved no shared state."""
    before = _shared_state(root, participant_hex)
    operation()
    assert _shared_state(root, participant_hex) == before


def _cloud_refusal_reason(call):
    with pytest.raises(SmallSeaCloudStorageRequired) as excinfo:
        call()
    return excinfo.value.reason


def test_linked_device_connects_credentials_to_the_inherited_account(
    playground_dir, minio_server_gen
):
    """The end-to-end shape issue #237 asks for, on two isolated installations.

    Device B inherits device A's account through NoteToSelf and cannot use it.
    Connecting credentials through the Manager makes B's own Hub I/O work
    against the same inherited account and allocation; disconnecting takes it
    away again, without either operation changing anything device A relies on.
    """
    minio = minio_server_gen(port=None)
    workspace = pathlib.Path(playground_dir)
    root_a = workspace / "install-a"
    root_b = workspace / "install-b"
    root_a.mkdir()
    root_b.mkdir()

    # --- Device A: identity, cloud account, allocation, first team ---
    alice_hex = provisioning.create_new_participant(root_a, "Alice")
    backend_a = SmallSea.SmallSeaBackend(root_dir=str(root_a), auto_approve_sessions=True)
    http_a = _use(backend_a)
    token_a = _open_note_to_self_session(http_a, "Alice")
    cloud_storage_id = backend_a.add_cloud_location(
        token_a, "s3", minio["endpoint"],
        access_key=minio["access_key"], secret_key=minio["secret_key"],
    )
    provisioning.add_berth_cloud_allocation_by_berth_id(
        root_a, alice_hex, backend_a._lookup_session(token_a).berth_id, cloud_storage_id,
    )
    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.create_team("SharedProject")
    manager_a.push_note_to_self()

    # --- Device B: bootstrap this identity onto a second installation ---
    join_request = create_identity_join_request(root_b)
    welcome = manager_a.authorize_identity_join(join_request["join_request_artifact"])
    bootstrap_existing_identity(root_b, welcome["welcome_bundle"], _http_client=http_a)

    backend_b = SmallSea.SmallSeaBackend(root_dir=str(root_b), auto_approve_sessions=True)
    http_b = _use(backend_b)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)

    # B inherited exactly A's account and allocation, and no credentials.
    inherited = _shared_state(root_b, alice_hex)
    assert inherited == _shared_state(root_a, alice_hex)
    accounts_b = manager_b.list_cloud_storage()
    assert len(accounts_b) == 1
    account_id = accounts_b[0]["id"]
    assert accounts_b[0]["credentials_on_this_device"] is False

    # 2. B's own Hub refuses the store operation for the reason that is true.
    assert _cloud_refusal_reason(manager_b.push_note_to_self) == "cloud_credentials_missing"

    # 3. B connects to the inherited account and the same operation works.
    _unchanged_by(root_b, alice_hex, lambda: manager_b.connect_cloud_storage_credentials(
        account_id,
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    ))
    assert manager_b.list_cloud_storage()[0]["credentials_on_this_device"] is True

    manager_b.create_team("DeviceBProject")
    manager_b.push_note_to_self()

    # The bytes really reached the shared location: A reads B's team back.
    http_a = _use(backend_a)
    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.refresh_note_to_self()
    assert "DeviceBProject" in {t["name"] for t in manager_a.list_known_teams()}

    # 4. Disconnecting takes the access away again, in the same Hub context,
    #    which no cached credential from step 3 may survive.
    http_b = _use(backend_b)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)
    manager_b.create_team("AfterDisconnectProject")
    _unchanged_by(
        root_b, alice_hex,
        lambda: manager_b.disconnect_cloud_storage_credentials(account_id),
    )
    assert manager_b.list_cloud_storage()[0]["credentials_on_this_device"] is False
    assert _cloud_refusal_reason(manager_b.push_note_to_self) == "cloud_credentials_missing"

    # 5. A's own credentials and allocation are untouched by all of that.
    http_a = _use(backend_a)
    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.refresh_note_to_self()
    assert manager_a.list_cloud_storage()[0]["credentials_on_this_device"] is True

    # 6. B reconnects and uses the same storage again.
    http_b = _use(backend_b)
    manager_b = TeamManager(root_b, alice_hex, _http_client=http_b)
    _unchanged_by(root_b, alice_hex, lambda: manager_b.connect_cloud_storage_credentials(
        account_id,
        access_key=minio["access_key"],
        secret_key=minio["secret_key"],
    ))
    manager_b.push_note_to_self()

    http_a = _use(backend_a)
    manager_a = TeamManager(root_a, alice_hex, _http_client=http_a)
    manager_a.refresh_note_to_self()
    assert "AfterDisconnectProject" in {t["name"] for t in manager_a.list_known_teams()}

    # Across the whole sequence no device registered another account, and the
    # account and allocation B inherited kept their IDs, locator, and
    # generation. Creating a team allocates that team's own Core berth, which
    # is ordinary provisioning rather than a credential effect; nothing removed
    # or rewrote what was already there. (Core storage announcements are not
    # involved: the NoteToSelf berth syncs in passthrough mode.)
    for root in (root_a, root_b):
        final = _shared_state(root, alice_hex)
        assert final["cloud_storage"] == inherited["cloud_storage"]
        for allocation in inherited["berth_cloud_allocation"]:
            assert allocation in final["berth_cloud_allocation"]
