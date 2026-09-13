"""Characterize prekey reuse and interruption with disposable local identities."""
import importlib.util
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


def test_interruption_after_prekey_consumption_prevents_retry(pair, monkeypatch):
    payload = artifact(pair)
    def interrupted(*args, **kwargs):
        raise RuntimeError('interrupted before persisting received sender key')
    with monkeypatch.context() as patch:
        patch.setattr(p, 'save_peer_sender_key', interrupted)
        with pytest.raises(RuntimeError, match='interrupted'):
            receive(pair, payload)
    with pytest.raises(ValueError, match='unavailable one-time prekey'):
        receive(pair, payload)
