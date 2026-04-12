from pathlib import Path
from types import SimpleNamespace

from cuttlefish.group import create_sender_key, group_encrypt
from small_sea_hub.crypto import decrypt_group_payload, serialize_group_message
from small_sea_manager.provisioning import create_new_participant, create_team
from small_sea_note_to_self.db import device_local_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    receiver_record_from_distribution,
    save_peer_sender_key,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair


def test_runtime_keeps_two_sender_devices_from_one_member_distinct(playground_dir):
    root = Path(playground_dir)
    bob_hex = create_new_participant(root, "Bob")
    team_result = create_team(root, bob_hex, "ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])

    # Alice conceptually has two linked devices; runtime lookup only sees the
    # device-key-derived sender handles and must keep both streams distinct.
    alice_device_d, _alice_device_d_private = generate_key_pair(ProtectionLevel.DAILY)
    alice_device_g, _alice_device_g_private = generate_key_pair(ProtectionLevel.DAILY)

    alice_d_sender_key, alice_d_distribution = create_sender_key(team_id, alice_device_d.key_id)
    alice_g_sender_key, alice_g_distribution = create_sender_key(team_id, alice_device_g.key_id)

    bob_local_db = device_local_db_path(root, bob_hex)
    save_peer_sender_key(
        bob_local_db, team_id, receiver_record_from_distribution(alice_d_distribution)
    )
    save_peer_sender_key(
        bob_local_db, team_id, receiver_record_from_distribution(alice_g_distribution)
    )

    bob_session = SimpleNamespace(
        participant_path=root / "Participants" / bob_hex,
        participant_id=bytes.fromhex(bob_hex),
        team_id=team_id,
        team_name="ProjectX",
    )

    alice_d_sender_key, alice_d_message = group_encrypt(
        team_id, alice_d_sender_key, b"from alice device d"
    )
    alice_g_sender_key, alice_g_message = group_encrypt(
        team_id, alice_g_sender_key, b"from alice device g"
    )

    assert decrypt_group_payload(
        bob_session, serialize_group_message(alice_d_message)
    ) == b"from alice device d"
    assert decrypt_group_payload(
        bob_session, serialize_group_message(alice_g_message)
    ) == b"from alice device g"

    alice_d_runtime = load_peer_sender_key(bob_local_db, team_id, alice_device_d.key_id)
    alice_g_runtime = load_peer_sender_key(bob_local_db, team_id, alice_device_g.key_id)
    assert alice_d_runtime is not None
    assert alice_g_runtime is not None
    assert alice_d_runtime.sender_device_key_id == alice_device_d.key_id
    assert alice_g_runtime.sender_device_key_id == alice_device_g.key_id
    assert alice_d_runtime.iteration == 1
    assert alice_g_runtime.iteration == 1
