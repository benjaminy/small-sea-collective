"""Characterize prekey reuse and interruption with disposable local identities."""
import importlib.util
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import pytest
from cuttlefish.prekeys import build_prekey_bundle, generate_identity_key_pair, generate_one_time_prekeys, generate_signed_prekey
from cuttlefish.x3dh import x3dh_send
from small_sea_manager import provisioning as p


def test_two_distinct_senders_choose_the_same_one_time_prekey():
    recipient = generate_identity_key_pair()
    signed, _ = generate_signed_prekey(recipient.signing_private_key)
    prekeys = generate_one_time_prekeys(20)
    bundle = build_prekey_bundle(b'recipient', recipient, signed, [key for key, _ in prekeys])
    first = x3dh_send(generate_identity_key_pair(), bundle)
    second = x3dh_send(generate_identity_key_pair(), bundle)
    assert first.initial_message.used_one_time_prekey_id == second.initial_message.used_one_time_prekey_id
    assert first.shared_secret != second.shared_secret


@pytest.fixture
def pair(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / 'packages/small-sea-manager/tests/test_sender_key_rotation.py'
    spec = importlib.util.spec_from_file_location('rotation_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', '/dev/null')
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    return module._bootstrap_remote_teammate_installation(tmp_path)


def artifact(pair):
    result = p.redistribute_sender_key(pair['alice_root'], pair['alice_hex'], 'ProjectX', [pair['bob_device_key_id']])
    return result['artifacts'][0]['distribution_payload']


def receive(pair, payload):
    return p.receive_sender_key_distribution(pair['bob_root'], pair['bob_hex'], 'ProjectX', payload)


def test_two_distributions_from_unchanged_bundle_cannot_both_be_received(pair):
    first, second = artifact(pair), artifact(pair)
    a, b = p._untokenize(first), p._untokenize(second)
    assert a['x3dh_initial_message']['used_one_time_prekey_id'] == b['x3dh_initial_message']['used_one_time_prekey_id']
    assert receive(pair, first)['target_device_key_id_hex'] == pair['bob_device_key_id'].hex()
    with pytest.raises(ValueError, match='unavailable one-time prekey'):
        receive(pair, second)


def stored_state(pair):
    with sqlite3.connect(p.device_local_db_path(pair['bob_root'], pair['bob_hex'])) as conn:
        return (
            conn.execute('SELECT * FROM redistribution_one_time_prekey ORDER BY prekey_id').fetchall(),
            conn.execute('SELECT * FROM peer_sender_key ORDER BY sender_device_key_id').fetchall(),
        )


@pytest.mark.parametrize('after_write', [False, True])
def test_interruption_rolls_back_prekey_and_receiver_record_and_allows_retry(pair, monkeypatch, after_write):
    p.rotate_team_sender_key(pair['alice_root'], pair['alice_hex'], 'ProjectX')
    payload = artifact(pair)
    before = stored_state(pair)
    save = p.save_peer_sender_key

    def interrupted(*args, **kwargs):
        if after_write:
            save(*args, **kwargs)
        raise RuntimeError('interrupted while persisting received sender key')

    with monkeypatch.context() as patch:
        patch.setattr(p, 'save_peer_sender_key', interrupted)
        with pytest.raises(RuntimeError, match='interrupted'):
            receive(pair, payload)
    assert stored_state(pair) == before
    assert receive(pair, payload)['target_device_key_id_hex'] == pair['bob_device_key_id'].hex()
    after = stored_state(pair)
    assert after[0] != before[0]
    assert after[1] != before[1]


@pytest.mark.parametrize('field', ['sender_device_key_id', 'sender_chain_id', 'group_id'])
def test_invalid_decrypted_distribution_does_not_consume_prekey(pair, monkeypatch, field):
    payload = artifact(pair)
    before = stored_state(pair)
    decrypt = p.ratchet_decrypt

    def invalid_distribution(*args, **kwargs):
        state, plaintext = decrypt(*args, **kwargs)
        data = p.json.loads(plaintext)
        data[field] = bytes(16).hex()
        return state, p._json_bytes(data)

    with monkeypatch.context() as patch:
        patch.setattr(p, 'ratchet_decrypt', invalid_distribution)
        with pytest.raises(ValueError, match='does not match'):
            receive(pair, payload)
    assert stored_state(pair) == before
    receive(pair, payload)


def test_concurrent_receivers_cannot_both_consume_prekey(pair, monkeypatch):
    payloads = [artifact(pair), artifact(pair)]
    decrypted = Barrier(2)
    decrypt = p.ratchet_decrypt
    save = p.save_peer_sender_key
    writes = []

    def synchronized_decrypt(*args, **kwargs):
        result = decrypt(*args, **kwargs)
        decrypted.wait(timeout=10)
        return result

    def track_save(*args, **kwargs):
        writes.append(args[2])
        return save(*args, **kwargs)

    monkeypatch.setattr(p, 'ratchet_decrypt', synchronized_decrypt)
    monkeypatch.setattr(p, 'save_peer_sender_key', track_save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(receive, pair, payload) for payload in payloads]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=15))
            except ValueError as error:
                outcomes.append(error)
    assert sum(isinstance(result, dict) for result in outcomes) == 1
    errors = [result for result in outcomes if isinstance(result, ValueError)]
    assert len(errors) == 1
    assert 'unavailable one-time prekey' in str(errors[0])
    assert len(writes) == 1
