"""Micro tests for the device projection used by storage announcements."""

import sqlite3
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from small_sea_hub.backend import SmallSeaBackend
from small_sea_hub.crypto import OwnershipProjectionAbsentExn
from wrasse_trust.keys import key_id_from_public
from wrasse_trust.transport import (
    TeammateBerthStorageAnnouncement,
    canonical_teammate_berth_storage_announcement_bytes,
    select_effective_teammate_berth_storage,
)


def test_storage_announcement_requires_device_projection(tmp_path, monkeypatch):
    backend = SmallSeaBackend(root_dir=tmp_path)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = key_id_from_public(public_key)
    teammate_id, berth_id, team_id = b"teammate", b"berth", b"team"
    unsigned = TeammateBerthStorageAnnouncement(
        announcement_id=b"announcement",
        teammate_id=teammate_id,
        berth_id=berth_id,
        protocol="s3",
        url="https://example.invalid",
        location="bucket",
        announced_at="2026-04-18T00:00:00+00:00",
        signer_key_id=key_id,
        signature=b"",
    )
    announcement = replace(
        unsigned,
        signature=private_key.sign(
            canonical_teammate_berth_storage_announcement_bytes(unsigned)
        ),
    )
    monkeypatch.setattr(
        backend, "_load_teammate_berth_storage_announcements", lambda *args: [announcement]
    )
    monkeypatch.setattr(backend, "_load_team_certificates", lambda *args: [])

    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(OwnershipProjectionAbsentExn):
            backend._select_teammate_berth_storage(
                conn, team_id, teammate_id, berth_id
            )

        conn.execute("CREATE TABLE team_device (device_key_id BLOB, public_key BLOB)")
        missing = backend._select_teammate_berth_storage(
            conn, team_id, teammate_id, berth_id
        )
        assert missing.status == "missing"

        conn.execute("INSERT INTO team_device VALUES (?, ?)", (key_id, public_key))
        accepted = select_effective_teammate_berth_storage(
            teammate_id=teammate_id,
            berth_id=berth_id,
            announcements=[announcement],
            team_id=team_id,
            trusted_public_keys={public_key},
            device_public_keys_by_key_id=backend._device_public_keys_by_key_id(conn),
        )
        assert accepted.status == "announced"
