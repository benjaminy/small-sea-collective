"""Micro tests for retaining independent receiver chains."""

from dataclasses import replace

import pytest
from cuttlefish.group import create_sender_key
from small_sea_note_to_self.db import initialize_device_local_db
from small_sea_note_to_self.sender_keys import (
    install_peer_sender_key,
    load_all_peer_sender_keys,
    load_peer_sender_key,
    receiver_record_from_distribution,
    save_peer_sender_key,
)


@pytest.mark.parametrize("reverse", [False, True])
def test_first_delivery_of_old_chain_preserves_new_chain(tmp_path, reverse):
    db = tmp_path / "local.db"
    initialize_device_local_db(db)
    records = [
        receiver_record_from_distribution(create_sender_key(b"team", b"device")[1])
        for _ in range(2)
    ]
    for record in reversed(records) if reverse else records:
        install_peer_sender_key(db, b"team", record)
    assert {record.chain_id for record in load_all_peer_sender_keys(db, b"team")} == {
        record.chain_id for record in records
    }
    for record in records:
        assert load_peer_sender_key(db, b"team", b"device", record.chain_id) == record


def test_decrypt_update_cannot_install_or_resurrect_a_chain(tmp_path):
    db = tmp_path / "local.db"
    initialize_device_local_db(db)
    record = receiver_record_from_distribution(create_sender_key(b"team", b"device")[1])
    with pytest.raises(ValueError, match="not installed"):
        save_peer_sender_key(db, b"team", record)
    assert load_all_peer_sender_keys(db, b"team") == []


def test_repeated_distribution_does_not_advance_or_rewind_receiver(tmp_path):
    db = tmp_path / "local.db"
    initialize_device_local_db(db)
    record = receiver_record_from_distribution(create_sender_key(b"team", b"device")[1])
    install_peer_sender_key(db, b"team", record)
    retained = replace(record, iteration=2, skipped_message_keys={0: b"retained"})
    save_peer_sender_key(db, b"team", retained)
    for repeated in (record, replace(record, iteration=10)):
        install_peer_sender_key(db, b"team", repeated)
        assert load_peer_sender_key(db, b"team", b"device", record.chain_id) == retained
