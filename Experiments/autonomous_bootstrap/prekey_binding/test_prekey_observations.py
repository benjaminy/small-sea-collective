"""Check the team-device signature before encrypting a sender-key distribution."""
import json
import sqlite3

import pytest

from small_sea_manager import provisioning as p
from small_sea_note_to_self.db import device_local_db_path
from small_sea_note_to_self.sender_keys import load_team_sender_key, distribution_message_from_record
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public


@pytest.mark.parametrize('attack', [
    'unsigned', 'attacker_signed', 'wrong_team', 'wrong_device',
    'wrong_participant', 'identity_dh', 'identity_signing', 'signed_prekey',
    'one_time_prekey', 'version', None,
])
def test_storage_selected_bundle_requires_device_signature(tmp_path, monkeypatch, attack):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'commit.gpgsign')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'false')
    root = tmp_path / 'alice'
    root.mkdir()
    participant = p.create_new_participant(root, 'Alice')
    team = p.create_team(root, participant, 'Research')
    team_id = bytes.fromhex(team['team_id_hex'])
    target, target_private = generate_key_pair(ProtectionLevel.DAILY)
    p.issue_device_link_for_teammate(root, participant, 'Research', target.public_key)
    target_id = key_id_from_public(target.public_key)

    # An attacker can replace the row, but has no private key for target.
    attacker = p.generate_identity_key_pair()
    signed_prekey, signed_private = p.generate_signed_prekey(attacker.signing_private_key)
    one_time = p.generate_one_time_prekeys(1)
    bundle = p.build_prekey_bundle(
        participant_id=b'wrong-recipient!' if attack == 'wrong_participant' else target_id,
        identity=attacker, signed_prekey=signed_prekey,
        one_time_prekeys=[one_time[0][0]],
    )
    wrapper = p._signed_device_prekey_bundle(
        team_id=b'other-team' if attack == 'wrong_team' else team_id,
        device_key_id=b'other-device' if attack == 'wrong_device' else target_id,
        prekey_bundle=bundle,
        private_key=attacker.signing_private_key if attack == 'attacker_signed' else target_private,
    )
    if attack == 'unsigned':
        wrapper = p._serialize_prekey_bundle(bundle)
    elif attack == 'version':
        wrapper['payload']['version'] = 2
    elif attack in ('identity_dh', 'identity_signing'):
        wrapper['payload']['prekey_bundle'][attack + '_public_key'] = ('00' * 32)
    elif attack == 'signed_prekey':
        wrapper['payload']['prekey_bundle']['signed_prekey']['public_key'] = '00' * 32
    elif attack == 'one_time_prekey':
        wrapper['payload']['prekey_bundle']['one_time_prekeys'][0]['public_key'] = '00' * 32
    db = p._team_db_path(root, participant, 'Research')
    with sqlite3.connect(db) as conn:
        conn.execute(
            'INSERT OR REPLACE INTO device_prekey_bundle '
            '(device_key_id, prekey_bundle_json, published_at) VALUES (?, ?, ?)',
            (target_id, json.dumps(wrapper), '2026-09-12T00:00:00Z'),
        )
    if attack is not None:
        def reject_encryption(*args, **kwargs):
            pytest.fail('Invalid bundle reached X3DH encryption')
        monkeypatch.setattr(p, 'x3dh_send', reject_encryption)
        with pytest.raises(ValueError, match='Invalid signed device prekey bundle'):
            p.redistribute_sender_key(root, participant, 'Research', [target_id])
        return
    result = p.redistribute_sender_key(root, participant, 'Research', [target_id])
    assert len(result['artifacts']) == 1
    payload = p._untokenize(result['artifacts'][0]['distribution_payload'])
    assert payload['target_device_key_id'] == target_id.hex()
    initial = p._deserialize_x3dh_initial_message(payload['x3dh_initial_message'])
    secret = p.x3dh_receive(attacker, signed_private, one_time[0][1], initial)
    state = p.initialize_as_receiver(secret, (signed_prekey.public_key, signed_private))
    associated = p._json_bytes({key: payload[key] for key in (
        'team_id', 'sender_device_key_id', 'target_device_key_id'
    )})
    _, plaintext = p.ratchet_decrypt(
        state, p._deserialize_encrypted_message(payload['ratchet_message']),
        associated_data=associated,
    )
    expected = distribution_message_from_record(load_team_sender_key(device_local_db_path(root, participant), team_id))
    assert json.loads(plaintext) == p.serialize_distribution_message(expected)
