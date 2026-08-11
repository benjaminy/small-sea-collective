import base64
import dataclasses
import json

import pytest

from small_sea_note_to_self.bootstrap import (
    JOIN_REQUEST_ARTIFACT_VERSION,
    JoinRequestArtifact,
    SIGNED_WELCOME_BUNDLE_VERSION,
    SignedWelcomeBundle,
    WELCOME_BUNDLE_VERSION,
    WelcomeBundle,
    deserialize_join_request_artifact,
    deserialize_signed_welcome_bundle_plaintext,
    deserialize_welcome_bundle_plaintext,
    join_request_auth_string,
    serialize_join_request_artifact,
    serialize_signed_welcome_bundle_plaintext,
    serialize_welcome_bundle_plaintext,
)


def _artifact(**overrides):
    return dataclasses.replace(
        JoinRequestArtifact(
            version=JOIN_REQUEST_ARTIFACT_VERSION,
            device_id_hex="aa" * 16,
            device_encryption_public_key_hex="bb" * 32,
            device_signing_public_key_hex="cc" * 32,
            device_label="Alice's phone",
        ),
        **overrides,
    )


def _bundle(**overrides):
    return dataclasses.replace(
        WelcomeBundle(
            version=WELCOME_BUNDLE_VERSION,
            participant_hex="dd" * 16,
            joining_device_id_hex="aa" * 16,
            joining_device_public_key_hex="bb" * 32,
            identity_label="Alice",
            remote_descriptor={"protocol": "localfolder", "url": "/tmp/cloud"},
            issued_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-01T00:10:00+00:00",
            authorizing_device_label="Alice",
        ),
        **overrides,
    )


def _signed(bundle=None, **overrides):
    return dataclasses.replace(
        SignedWelcomeBundle(
            version=SIGNED_WELCOME_BUNDLE_VERSION,
            bundle=bundle if bundle is not None else _bundle(),
            authorizing_device_id_hex="ee" * 16,
            signature_hex="ff" * 64,
        ),
        **overrides,
    )


def test_join_request_artifact_round_trip():
    artifact = _artifact()

    assert deserialize_join_request_artifact(
        serialize_join_request_artifact(artifact)
    ) == artifact


def test_join_request_artifact_round_trips_an_absent_device_label():
    artifact = _artifact(device_label=None)

    restored = deserialize_join_request_artifact(serialize_join_request_artifact(artifact))

    assert restored == artifact
    assert restored.device_label is None


def test_join_request_artifact_rejects_a_non_string_device_label():
    payload = json.loads(
        base64.b64decode(serialize_join_request_artifact(_artifact())).decode("utf-8")
    )
    payload["device_label"] = 42
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    with pytest.raises(ValueError, match="device_label"):
        deserialize_join_request_artifact(encoded)


def test_device_label_is_covered_by_the_authentication_string():
    """Structural, since the digest is over asdict of the whole artifact.

    Asserted so a future hand-written canonicalization cannot silently drop the field,
    but this is not independent evidence that the label is bound.
    """
    assert join_request_auth_string(_artifact(device_label="Alice's phone")) != (
        join_request_auth_string(_artifact(device_label="Alice's laptop"))
    )


def test_welcome_bundle_round_trip():
    bundle = _bundle()

    assert deserialize_welcome_bundle_plaintext(
        serialize_welcome_bundle_plaintext(bundle)
    ) == bundle


def test_signed_welcome_bundle_round_trip():
    signed = _signed()

    assert deserialize_signed_welcome_bundle_plaintext(
        serialize_signed_welcome_bundle_plaintext(signed)
    ) == signed


def test_unsupported_join_request_artifact_version_is_rejected():
    encoded = serialize_join_request_artifact(
        _artifact(version=JOIN_REQUEST_ARTIFACT_VERSION + 1)
    )

    with pytest.raises(ValueError) as exc_info:
        deserialize_join_request_artifact(encoded)

    message = str(exc_info.value)
    assert "join request artifact" in message
    assert str(JOIN_REQUEST_ARTIFACT_VERSION + 1) in message


def test_unsupported_welcome_bundle_version_is_rejected():
    plaintext = serialize_welcome_bundle_plaintext(
        _bundle(version=WELCOME_BUNDLE_VERSION + 1)
    )

    with pytest.raises(ValueError) as exc_info:
        deserialize_welcome_bundle_plaintext(plaintext)

    assert "welcome bundle" in str(exc_info.value)


def test_unsupported_signed_wrapper_version_is_rejected():
    plaintext = serialize_signed_welcome_bundle_plaintext(
        _signed(version=SIGNED_WELCOME_BUNDLE_VERSION + 1)
    )

    with pytest.raises(ValueError) as exc_info:
        deserialize_signed_welcome_bundle_plaintext(plaintext)

    assert "signed welcome bundle" in str(exc_info.value)


def test_unsupported_nested_bundle_version_is_rejected_through_the_wrapper():
    """A supported wrapper does not excuse an unsupported payload inside it."""
    plaintext = serialize_signed_welcome_bundle_plaintext(
        _signed(bundle=_bundle(version=WELCOME_BUNDLE_VERSION + 1))
    )

    with pytest.raises(ValueError) as exc_info:
        deserialize_signed_welcome_bundle_plaintext(plaintext)

    assert "welcome bundle" in str(exc_info.value)


@pytest.mark.parametrize(
    ("plaintext", "deserialize"),
    [
        (
            serialize_join_request_artifact(
                _artifact(version=float(JOIN_REQUEST_ARTIFACT_VERSION))
            ),
            deserialize_join_request_artifact,
        ),
        (
            serialize_welcome_bundle_plaintext(_bundle(version=True)),
            deserialize_welcome_bundle_plaintext,
        ),
        (
            serialize_signed_welcome_bundle_plaintext(_signed(version=True)),
            deserialize_signed_welcome_bundle_plaintext,
        ),
    ],
)
def test_non_integer_versions_are_rejected(plaintext, deserialize):
    with pytest.raises(ValueError, match="version"):
        deserialize(plaintext)


def test_missing_version_is_rejected_before_other_fields_are_read():
    payload = {
        "device_id_hex": "aa" * 16,
        "device_encryption_public_key_hex": "bb" * 32,
        "device_signing_public_key_hex": "cc" * 32,
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    with pytest.raises(ValueError) as exc_info:
        deserialize_join_request_artifact(encoded)

    assert "None" in str(exc_info.value)


def test_non_object_payload_is_rejected_as_a_version_failure():
    encoded = base64.b64encode(json.dumps(["not", "an", "artifact"]).encode("utf-8")).decode("ascii")

    with pytest.raises(ValueError):
        deserialize_join_request_artifact(encoded)
