"""Observe whether authentic delayed distributions replace newer receiver state."""
import importlib.util
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from small_sea_manager import provisioning as p


@pytest.fixture
def pair(tmp_path, monkeypatch):
    helper = Path(__file__).resolve().parents[2] / 'packages/small-sea-manager/tests/test_sender_key_rotation.py'
    spec = importlib.util.spec_from_file_location('rotation_fixture', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', '/dev/null')
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    return module._bootstrap_remote_teammate_installation(tmp_path)


def distribute(pair):
    return p.redistribute_sender_key(pair['alice_root'], pair['alice_hex'], 'ProjectX',
                                    [pair['bob_device_key_id']])['artifacts'][0]['distribution_payload']


def receive(pair, payload):
    return p.receive_sender_key_distribution(pair['bob_root'], pair['bob_hex'], 'ProjectX', payload)


def chain(pair, sender):
    with sqlite3.connect(p.device_local_db_path(pair['bob_root'], pair['bob_hex'])) as conn:
        rows = conn.execute('SELECT chain_id FROM peer_sender_key WHERE sender_device_key_id = ?',
                            (bytes.fromhex(sender),)).fetchall()
    assert len(rows) == 1
    return rows[0][0].hex()


@pytest.mark.parametrize('reverse', [False, True], ids=['ordered-control', 'delayed-old-distribution'])
def test_last_delivery_replaces_current_chain_even_when_older(pair, monkeypatch, reverse):
    old = distribute(pair)
    p.rotate_team_sender_key(pair['alice_root'], pair['alice_hex'], 'ProjectX')
    send = p.x3dh_send

    def choose_second_advertised_prekey(identity, bundle):
        # A sender may choose another advertised prekey; all private keys stay
        # with Bob. This avoids the known first-prekey collision obscuring order.
        assert len(bundle.one_time_prekeys) >= 2
        return send(identity, replace(bundle, one_time_prekeys=bundle.one_time_prekeys[1:]))

    with monkeypatch.context() as patch:
        patch.setattr(p, 'x3dh_send', choose_second_advertised_prekey)
        new = distribute(pair)
    old_body, new_body = p._untokenize(old), p._untokenize(new)
    old_chain, new_chain = old_body['sender_chain_id'], new_body['sender_chain_id']
    assert old_chain != new_chain
    assert old_body['x3dh_initial_message']['used_one_time_prekey_id'] != new_body['x3dh_initial_message']['used_one_time_prekey_id']
    first, last = (new, old) if reverse else (old, new)
    sender = old_body['sender_device_key_id']
    receive(pair, first)
    assert chain(pair, sender) == (new_chain if reverse else old_chain)
    receive(pair, last)
    assert chain(pair, sender) == (old_chain if reverse else new_chain)
    # Receipt protection still works for exact replay of a consumed prekey.
    with pytest.raises(ValueError, match='unavailable one-time prekey'):
        receive(pair, last)
