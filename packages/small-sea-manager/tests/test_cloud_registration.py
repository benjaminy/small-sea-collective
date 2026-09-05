"""Micro tests for Manager cloud account registration (issue #181).

Account registration moved out of the Hub. These tests witness the contract it
now carries on the Manager side: which protocols it accepts, that a rejected
protocol leaves both databases untouched, and that registering an account is
not by itself an allocation decision.
"""

import pathlib
import sqlite3

import pytest
import small_sea_manager.provisioning as provisioning
from small_sea_note_to_self.db import (
    device_local_db_path,
    note_to_self_sync_db_path,
)


def _shared_rows(root, participant_hex):
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


def _credential_rows(root, participant_hex):
    with sqlite3.connect(device_local_db_path(root, participant_hex)) as conn:
        return conn.execute(
            "SELECT cloud_storage_id, access_key, secret_key, client_secret,"
            " refresh_token, access_token, token_expiry"
            " FROM cloud_storage_credential ORDER BY rowid"
        ).fetchall()


def test_registration_accepts_exactly_the_declared_protocols(playground_dir):
    """The accepted set is the explicit contract, not whatever a caller passes.

    `localfolder` is in it because Manager callers depend on it; dropping it
    would have narrowed the API while moving registration off the Hub.
    """
    root = pathlib.Path(playground_dir)
    alice_hex = provisioning.create_new_participant(root, "Alice")

    assert provisioning.SUPPORTED_CLOUD_PROTOCOLS == (
        "s3", "webdav", "gdrive", "dropbox", "localfolder",
    )

    for protocol in provisioning.SUPPORTED_CLOUD_PROTOCOLS:
        storage_id = provisioning.add_cloud_storage(
            root, alice_hex, protocol=protocol, url=f"http://example.invalid/{protocol}"
        )
        assert isinstance(storage_id, str)

    stored = {row[1] for row in _shared_rows(root, alice_hex)["cloud_storage"]}
    assert stored == set(provisioning.SUPPORTED_CLOUD_PROTOCOLS)


def test_unknown_protocol_is_rejected_before_either_database_changes(playground_dir):
    """A bad protocol leaves no shared account row and no device-local credential row.

    Registration writes to two databases, so rejecting late would leave a
    half-registered account behind.
    """
    root = pathlib.Path(playground_dir)
    alice_hex = provisioning.create_new_participant(root, "Alice")
    provisioning.add_cloud_storage(
        root, alice_hex, protocol="s3", url="http://localhost:9000",
        access_key="key", secret_key="secret",
    )

    shared_before = _shared_rows(root, alice_hex)
    credentials_before = _credential_rows(root, alice_hex)

    with pytest.raises(provisioning.UnknownCloudProtocolError) as excinfo:
        provisioning.add_cloud_storage(
            root, alice_hex, protocol="ftp", url="ftp://example.invalid",
            access_key="key", secret_key="secret",
        )
    assert excinfo.value.protocol == "ftp"

    assert _shared_rows(root, alice_hex) == shared_before
    assert _credential_rows(root, alice_hex) == credentials_before


def test_registration_makes_no_allocation_decision(playground_dir):
    """Registering an account does not place any berth on it.

    Allocation is a separate Manager decision; if registration created one
    silently, the Hub's locator writeback would have somewhere to land that
    nobody chose.
    """
    root = pathlib.Path(playground_dir)
    alice_hex = provisioning.create_new_participant(root, "Alice")
    allocations_before = _shared_rows(root, alice_hex)["berth_cloud_allocation"]

    provisioning.add_cloud_storage(
        root, alice_hex, protocol="s3", url="http://localhost:9000",
        access_key="key", secret_key="secret",
    )

    assert _shared_rows(root, alice_hex)["berth_cloud_allocation"] == allocations_before


def test_credentials_stay_out_of_the_shared_account_row(playground_dir):
    """Secret material lands device-local only; the shared row carries no secret."""
    root = pathlib.Path(playground_dir)
    alice_hex = provisioning.create_new_participant(root, "Alice")

    storage_id = provisioning.add_cloud_storage(
        root, alice_hex, protocol="s3", url="http://localhost:9000",
        access_key="access-key-material", secret_key="secret-key-material",
        client_id="client-id", client_secret="client-secret-material",
        refresh_token="refresh-material", access_token="access-material",
        token_expiry="2099-01-01T00:00:00+00:00",
    )

    shared = _shared_rows(root, alice_hex)["cloud_storage"]
    assert len(shared) == 1
    assert shared[0][0].hex() == storage_id
    # client_id is shared by design; nothing else on the row is secret material.
    assert shared[0][3] == "client-id"
    assert "secret-key-material" not in repr(shared)
    assert "refresh-material" not in repr(shared)

    credentials = _credential_rows(root, alice_hex)
    assert len(credentials) == 1
    assert credentials[0][1:] == (
        "access-key-material", "secret-key-material", "client-secret-material",
        "refresh-material", "access-material", "2099-01-01T00:00:00+00:00",
    )
