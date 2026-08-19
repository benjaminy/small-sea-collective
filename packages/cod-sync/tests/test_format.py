"""Micro tests for the version-2 link codec.

The core of the format is strict and its extension point is open, and both
halves matter: a decoder that shrugged at an unknown top-level key would let a
future semantic change pass as noise, while one that dropped unknown
extensions would silently break another implementation's signatures.
"""

import pytest
import yaml

from cod_sync.format import (
    COD_SYNC_VERSION,
    Link,
    LinkFormatError,
    Predecessor,
    UnsupportedLinkVersionError,
    canonical_link_bytes,
    decode_link,
    encode_link,
    parse_version,
    sign_link,
    signed_link,
    verify_link_signature,
)

HEAD_A = "a" * 40
HEAD_B = "b" * 40
LINK_A = "1" * 16
LINK_B = "2" * 16
BUNDLE_A = "a" * 16
BUNDLE_B = "b" * 16


def initial_link(**overrides) -> Link:
    fields = dict(link_id=LINK_A, head=HEAD_A, bundle_id=BUNDLE_A, previous=None)
    fields.update(overrides)
    return Link(**fields)


def incremental_link(**overrides) -> Link:
    fields = dict(
        link_id=LINK_B,
        head=HEAD_B,
        bundle_id=BUNDLE_B,
        previous=Predecessor(link_id=LINK_A, head=HEAD_A),
    )
    fields.update(overrides)
    return Link(**fields)


def mapping_of(link: Link) -> dict:
    return yaml.safe_load(encode_link(link).decode("utf-8"))


def bytes_of(mapping: dict) -> bytes:
    return yaml.dump(mapping, default_flow_style=False, sort_keys=True).encode("utf-8")


def test_version_is_two():
    assert COD_SYNC_VERSION == "2.0.0"
    assert parse_version(COD_SYNC_VERSION) == (2, 0, 0)


def test_initial_link_round_trips():
    link = initial_link()
    decoded = decode_link(encode_link(link))
    assert decoded == link
    assert decoded.previous is None


def test_incremental_link_round_trips():
    link = incremental_link()
    decoded = decode_link(encode_link(link))
    assert decoded.previous == Predecessor(link_id=LINK_A, head=HEAD_A)


def test_encoded_shape_is_the_documented_mapping():
    mapping = mapping_of(incremental_link())
    assert set(mapping) == {
        "version",
        "link_id",
        "head",
        "bundle_id",
        "previous",
        "extensions",
    }
    assert mapping["previous"] == {"link_id": LINK_A, "head": HEAD_A}


def test_unknown_top_level_key_is_rejected():
    mapping = mapping_of(initial_link())
    mapping["branches"] = [["main", HEAD_A]]
    with pytest.raises(LinkFormatError, match="unknown keys"):
        decode_link(bytes_of(mapping))


def test_missing_top_level_key_is_rejected():
    mapping = mapping_of(initial_link())
    del mapping["bundle_id"]
    with pytest.raises(LinkFormatError, match="missing keys"):
        decode_link(bytes_of(mapping))


@pytest.mark.parametrize(
    "previous",
    [
        {"link_id": LINK_A},
        {"link_id": LINK_A, "head": HEAD_A, "extra": 1},
        {"link_id": LINK_A, "head": ""},
        [LINK_A, HEAD_A],
        LINK_A,
    ],
)
def test_malformed_previous_is_rejected(previous):
    mapping = mapping_of(incremental_link())
    mapping["previous"] = previous
    with pytest.raises(LinkFormatError):
        decode_link(bytes_of(mapping))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("link_id", "/tmp/peer-controlled"),
        ("link_id", "A" * 16),
        ("bundle_id", "../outside-temp"),
        ("bundle_id", "f" * 15),
        ("head", "HEAD"),
        ("head", "A" * 40),
    ],
)
def test_identifiers_must_match_the_wire_format(field, value):
    mapping = mapping_of(initial_link())
    mapping[field] = value
    with pytest.raises(LinkFormatError, match=field):
        decode_link(bytes_of(mapping))


@pytest.mark.parametrize(
    ("field", "value"),
    [("link_id", "../previous"), ("head", "main")],
)
def test_previous_identifiers_must_match_the_wire_format(field, value):
    mapping = mapping_of(incremental_link())
    mapping["previous"][field] = value
    with pytest.raises(LinkFormatError, match=f"previous.{field}"):
        decode_link(bytes_of(mapping))


def test_a_link_that_is_not_a_mapping_is_rejected():
    # The version-1 shape was a positional list, so this is also the shape a
    # v1 store would present.
    with pytest.raises(LinkFormatError, match="must be a mapping"):
        decode_link(b"- [a, b]\n- []\n")


def test_unreadable_bytes_are_rejected():
    with pytest.raises(LinkFormatError):
        decode_link(b"\x00\x01 not yaml: [")


@pytest.mark.parametrize("version", ["1.0.0", "3.0.0", "17.2.1"])
def test_unsupported_major_is_an_upgrade_not_corruption(version):
    mapping = mapping_of(initial_link())
    mapping["version"] = version
    with pytest.raises(UnsupportedLinkVersionError) as exc:
        decode_link(bytes_of(mapping))
    assert exc.value.version == version
    assert not isinstance(exc.value, LinkFormatError)


def test_malformed_version_is_a_format_error():
    mapping = mapping_of(initial_link())
    mapping["version"] = "two"
    with pytest.raises(LinkFormatError):
        decode_link(bytes_of(mapping))


def test_unknown_extensions_survive_a_round_trip():
    link = initial_link(extensions={"weather": {"sky": "grey"}, "n": 3})
    decoded = decode_link(encode_link(link))
    assert decoded.extensions["weather"] == {"sky": "grey"}
    assert decoded.extensions["n"] == 3


def test_extensions_must_be_a_mapping():
    mapping = mapping_of(initial_link())
    mapping["extensions"] = ["signatures"]
    with pytest.raises(LinkFormatError, match="extensions"):
        decode_link(bytes_of(mapping))


def test_canonical_bytes_cover_unknown_extensions():
    plain = canonical_link_bytes(initial_link())
    extended = canonical_link_bytes(initial_link(extensions={"weather": "grey"}))
    assert plain != extended


def test_canonical_bytes_exclude_only_signatures():
    link = initial_link(extensions={"weather": "grey"})
    with_sig = link.with_extensions(
        {"weather": "grey", "signatures": {"alice": {"signature": "x"}}}
    )
    assert canonical_link_bytes(link) == canonical_link_bytes(with_sig)


def test_signing_round_trip_covers_extensions():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    link = initial_link(extensions={"weather": "grey"})
    signed = signed_link(link, private_bytes, "alice", public_bytes)
    decoded = decode_link(encode_link(signed))

    signature = decoded.extensions["signatures"]["alice"]["signature"]
    assert verify_link_signature(
        public_bytes, signature, canonical_link_bytes(decoded)
    )

    # A change to a covered field breaks the signature.
    tampered = decoded.with_extensions({**decoded.extensions, "weather": "blue"})
    assert not verify_link_signature(
        public_bytes, signature, canonical_link_bytes(tampered)
    )


def test_signature_needs_a_device_public_key():
    with pytest.raises(ValueError, match="device_public_key"):
        signed_link(initial_link(), b"\x00" * 32, "alice", None)


def test_sign_link_is_deterministic_over_canonical_bytes():
    key = b"\x11" * 32
    canonical = canonical_link_bytes(initial_link())
    assert sign_link(key, canonical) == sign_link(key, canonical)
