"""Micro tests for the Hub's publication boundary.

Everything here uses real keys, real sender-key records and real SQLite state
in a temporary directory. What each test varies is the evidence a read is given
— the expected context, the expected publisher, and Core's accepted device
ownership — because that is what decides whether bytes are an acceptable
publication.
"""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from cuttlefish.group import create_sender_key, group_encrypt
from small_sea_hub.crypto import (
    DeviceOwnershipAmbiguousExn,
    DeviceOwnershipUnavailableExn,
    OwnershipProjectionAbsentExn,
    PublicationNotAuthenticExn,
    UnexpectedPublisherExn,
    decrypt_group_payload,
    deserialize_group_message,
    publication_context,
    serialize_group_message,
)
from small_sea_manager.provisioning import create_new_participant, create_team
from small_sea_note_to_self.db import device_local_db_path
from small_sea_note_to_self.sender_keys import (
    load_peer_sender_key,
    receiver_record_from_distribution,
    save_peer_sender_key,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair

TEAMMATE_ALICE = b"alice-teammate--"
TEAMMATE_CAROL = b"carol-teammate--"
BERTH = b"berth-id--------"
PATH = "chains/latest-link.yaml"


@pytest.fixture()
def reader(playground_dir):
    """Bob's real local state, plus Alice's two devices publishing into it.

    Alice's devices are separate sender chains that Core associates with one
    teammate, which is what a linked-device pair looks like to a reader.
    """
    root = Path(playground_dir)
    bob_hex = create_new_participant(root, "Bob")
    team_result = create_team(root, bob_hex, "ProjectX")
    team_id = bytes.fromhex(team_result["team_id_hex"])
    bob_local_db = device_local_db_path(root, bob_hex)

    devices = {}
    for name in ("alice_d", "alice_g", "carol"):
        device, _private = generate_key_pair(ProtectionLevel.DAILY)
        record, distribution = create_sender_key(team_id, device.key_id)
        save_peer_sender_key(
            bob_local_db, team_id, receiver_record_from_distribution(distribution)
        )
        devices[name] = record

    session = SimpleNamespace(
        participant_path=root / "Participants" / bob_hex,
        participant_id=bytes.fromhex(bob_hex),
        team_id=team_id,
        team_name="ProjectX",
        berth_id=BERTH,
    )
    ownership = {
        devices["alice_d"].sender_device_key_id: {TEAMMATE_ALICE},
        devices["alice_g"].sender_device_key_id: {TEAMMATE_ALICE},
        devices["carol"].sender_device_key_id: {TEAMMATE_CAROL},
    }
    return SimpleNamespace(
        root=root,
        participant=bob_hex,
        local_db=bob_local_db,
        team_id=team_id,
        session=session,
        devices=devices,
        ownership=ownership,
        context=publication_context(team_id, BERTH, PATH),
    )


def publish(env, device_name, plaintext, context=None):
    record, message = group_encrypt(
        env.team_id,
        env.devices[device_name],
        plaintext,
        env.context if context is None else context,
    )
    env.devices[device_name] = record
    return serialize_group_message(message)


def accept(env, payload, *, context=None, publisher=TEAMMATE_ALICE, ownership=True):
    return decrypt_group_payload(
        env.session,
        payload,
        expected_context=env.context if context is None else context,
        expected_publisher=publisher,
        device_ownership=env.ownership if ownership is True else ownership,
    )


def _with_extra_ciphertext(payload):
    message = deserialize_group_message(payload)
    return serialize_group_message(
        replace(message, ciphertext=message.ciphertext + b"\x00")
    )


def receiver_state(env):
    return {
        name: load_peer_sender_key(
            env.local_db, env.team_id, record.sender_device_key_id
        )
        for name, record in env.devices.items()
    }


# --- Multiple devices for one teammate ---


def test_two_devices_of_one_teammate_are_both_accepted_and_stay_distinct(reader):
    env = reader
    assert accept(env, publish(env, "alice_d", b"from device d")) == b"from device d"
    assert accept(env, publish(env, "alice_g", b"from device g")) == b"from device g"

    runtime = receiver_state(env)
    assert (
        runtime["alice_d"].sender_device_key_id
        != runtime["alice_g"].sender_device_key_id
    )
    assert runtime["alice_d"].iteration == runtime["alice_g"].iteration == 1


def test_a_device_of_another_teammate_is_refused_though_its_key_is_available(reader):
    env = reader
    payload = publish(env, "carol", b"from carol")

    with pytest.raises(UnexpectedPublisherExn):
        accept(env, payload)

    # Carol's own read of the same bytes succeeds, so the refusal was about the
    # expected publisher and not about the key or the context.
    assert accept(env, payload, publisher=TEAMMATE_CAROL) == b"from carol"


def test_a_device_core_does_not_know_yields_the_pending_result(reader):
    env = reader
    payload = publish(env, "alice_d", b"from an unrecognized device")
    without_alice_d = {
        device: owners
        for device, owners in env.ownership.items()
        if device != env.devices["alice_d"].sender_device_key_id
    }

    with pytest.raises(DeviceOwnershipUnavailableExn):
        accept(env, payload, ownership=without_alice_d)


def test_bytes_that_fail_authentication_are_refused_rather_than_left_pending(reader):
    env = reader
    payload = publish(env, "alice_d", b"published")
    without_alice_d = {
        device: owners
        for device, owners in env.ownership.items()
        if device != env.devices["alice_d"].sender_device_key_id
    }

    # Missing ownership evidence is only worth waiting for when the bytes
    # themselves verify; altered bytes are refused however long one waits.
    with pytest.raises(PublicationNotAuthenticExn) as refused:
        accept(env, _with_extra_ciphertext(payload), ownership=without_alice_d)
    assert refused.value.reason == "invalid_signature"

    with pytest.raises(DeviceOwnershipUnavailableExn):
        accept(env, payload, ownership=without_alice_d)


def test_an_absent_projection_is_its_own_outcome(reader):
    env = reader
    payload = publish(env, "alice_d", b"published")

    with pytest.raises(OwnershipProjectionAbsentExn):
        accept(env, payload, ownership=None)

    # An empty mapping is a projection with no matching row, not permission to
    # skip the check.
    with pytest.raises(DeviceOwnershipUnavailableExn):
        accept(env, payload, ownership={})


def test_competing_associations_are_left_for_a_person(reader):
    env = reader
    payload = publish(env, "alice_d", b"published")
    contested = dict(env.ownership)
    contested[env.devices["alice_d"].sender_device_key_id] = {
        TEAMMATE_ALICE,
        TEAMMATE_CAROL,
    }

    with pytest.raises(DeviceOwnershipAmbiguousExn):
        accept(env, payload, ownership=contested)


# --- Object context ---


@pytest.mark.parametrize(
    "substituted",
    [
        "another path in the same berth",
        "another berth in the same team",
        "another team",
    ],
)
def test_a_valid_payload_does_not_move_between_contexts(reader, substituted):
    env = reader
    published_for = {
        "another path in the same berth": publication_context(
            env.team_id, BERTH, "chains/other-link.yaml"
        ),
        "another berth in the same team": publication_context(
            env.team_id, b"other-berth-----", PATH
        ),
        "another team": publication_context(b"other-team-id---", BERTH, PATH),
    }[substituted]
    payload = publish(env, "alice_d", b"published elsewhere", context=published_for)

    with pytest.raises(PublicationNotAuthenticExn) as refused:
        accept(env, payload)
    assert refused.value.reason == "context_mismatch"

    # Same fixture, same key, correct expectations: accepted.
    assert accept(env, payload, context=published_for) == b"published elsewhere"


def test_context_and_ownership_refusals_are_told_apart(reader):
    env = reader
    elsewhere = publication_context(env.team_id, BERTH, "chains/other-link.yaml")

    # The key is deliberately available for both, so only the checked evidence
    # differs.
    with pytest.raises(PublicationNotAuthenticExn) as by_context:
        accept(env, publish(env, "alice_d", b"a", context=elsewhere))
    assert by_context.value.reason == "context_mismatch"

    with pytest.raises(UnexpectedPublisherExn) as by_owner:
        accept(env, publish(env, "carol", b"b"))
    assert by_owner.value.reason == "unexpected_publisher"


def test_logical_paths_that_differ_at_all_are_different_objects(reader):
    env = reader
    paths = [
        "percent%2Fname",
        "percent/name",
        "plus+name",
        "plus name",
        "café",
        "café",
    ]
    contexts = {publication_context(env.team_id, BERTH, path) for path in paths}
    assert len(contexts) == len(paths)


# --- Refusals leave nothing behind ---


@pytest.mark.parametrize("iteration_position", ["current", "future", "retained"])
def test_a_refused_read_changes_no_receiver_state(reader, iteration_position):
    env = reader
    if iteration_position == "future":
        # Advance the sender without letting the reader see the messages.
        publish(env, "alice_d", b"unseen")
        publish(env, "alice_d", b"also unseen")
    payload = publish(env, "alice_d", b"the real object")
    if iteration_position == "retained":
        assert accept(env, payload) == b"the real object"

    before = receiver_state(env)
    forgeries = [
        publish(env, "carol", b"wrong writer"),
        publish(
            env,
            "alice_d",
            b"wrong context",
            context=publication_context(env.team_id, BERTH, "chains/other.yaml"),
        ),
        _with_extra_ciphertext(payload),
    ]
    for forgery in forgeries:
        with pytest.raises((PublicationNotAuthenticExn, UnexpectedPublisherExn)):
            accept(env, forgery)
    assert receiver_state(env) == before

    # The original still reads afterwards, at every position.
    assert accept(env, payload) == b"the real object"


def test_an_unbound_envelope_is_refused_rather_than_read(reader):
    env = reader
    payload = publish(env, "alice_d", b"published")
    unbound = json.loads(payload.decode("utf-8"))
    del unbound["format"]

    with pytest.raises(PublicationNotAuthenticExn) as refused:
        accept(env, json.dumps(unbound).encode("utf-8"))
    assert refused.value.reason == "unreadable_envelope"
