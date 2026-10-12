"""The verifier, run as a fresh process on the bundle from scenario step 8.

Expected values are literals: member names, device labels, check names.
"""

import json
import subprocess
import sys

import pytest

from small_sea_manager.spine import encoding
from small_sea_manager.spine.device import Refused
from small_sea_manager.spine.scenario import GENESIS_TIME, KEY, SEEDS, Scenario


def run_story(tmp_path):
    s = Scenario(tmp_path)
    s.step1_carol_adds_phone()
    s.step2_alice_removes_bob_and_bob_adds_device()
    s.step3a_carol_gets_bobs_device()
    s.step3b_everyone_sees_both()
    with pytest.raises(Refused):
        s.step4_carol_tries_tablet()
    s.step5_alice_reconciles()
    s.step6a_bob_and_carol_get_r()
    s.step6b_carol_accepts_bob_refuses()
    s.step7_carol_adds_tablet()
    s.step8_carol_exports()
    return s


def verify(path, origin, *flags):
    return subprocess.run(
        [sys.executable, "-m", "small_sea_manager.spine.verify", "--origin", origin, str(path), *flags],
        capture_output=True, text=True)


def verify_json(path, origin):
    done = verify(path, origin, "--json")
    return done.returncode, json.loads(done.stdout)


def tables(state):
    out = {row["name"]: [] for row in state["members"].values()}
    for row in state["devices"].values():
        out[state["members"][row["member_id"]]["name"]].append(row["label"])
    return {name: sorted(labels) for name, labels in out.items()}


def rewrite_bundle(origin, tmp_path, tip, objects):
    path = tmp_path / "altered.json"
    path.write_bytes(encoding.make_bundle(origin, tip, objects))
    return path


def bundle_objects(s):
    hexes = encoding.decode_canonical(s.bundle_path.read_bytes())["objects"]
    return {s.names[encoding.decode_object(bytes.fromhex(h))["id"]]: bytes.fromhex(h) for h in hexes}


@pytest.fixture(scope="module")
def story(tmp_path_factory):
    return run_story(tmp_path_factory.mktemp("story"))


def test_the_story_bundle_is_valid_and_explained(story):
    s = story
    code, result = verify_json(s.bundle_path, s.origin)
    assert code == 0
    assert result["status"] == "valid"
    name = lambda i: s.names[i]
    assert [(name(c["event"]), c["description"]) for c in result["chain"]] == [
        ("G", "the team begins with Alice: a; Bob: b; Carol: c"),
        ("P", "Carol (device c) adds device c2"),
        ("R", f"Alice (device a) reconciles: keeps {s.id('X')[:8]}; rejects {s.id('Y')[:8]}"),
        ("c3", "Carol (device c) adds device c3"),
    ]
    assert name(result["base"]) == "P"
    assert [(name(b["event"]), b["description"], b["valid_at_parent"], tables(b["state_after"]))
            for b in result["branches"]] == [
        ("X", "Alice (device a) removes Bob", True, {"Alice": ["a"], "Carol": ["c", "c2"]}),
        ("Y", "Bob (device b) adds device b2", True,
         {"Alice": ["a"], "Bob": ["b", "b2"], "Carol": ["c", "c2"]}),
    ]
    assert result["conflict"] == {
        "reason": "some order fails or the orders disagree",
        "rejected_after_kept": {s.id("Y"): "signer is not a device"},
    }
    r = result["reconciliation"]
    assert (name(r["id"]), r["signer"]) == ("R", "Alice (device a)")
    assert ([name(e) for e in r["adopted"]], [name(e) for e in r["rejected"]]) == (["X"], ["Y"])
    assert tables(result["final_state"]) == {"Alice": ["a"], "Carol": ["c", "c2", "c3"]}
    assert "cannot say what any device received" in result["scope"]


def test_the_text_explanation_says_bob_removed_and_b2_not_added(story):
    done = verify(story.bundle_path, story.origin)
    assert done.returncode == 0
    text = done.stdout
    assert "Alice (device a) removes Bob (kept)" in text
    assert "Bob (device b) adds device b2 (rejected)" in text
    assert "This history produces: Alice: a; Carol: c, c2, c3" in text
    assert "Other devices may follow different histories" in text
    assert f"Alice (device a) rejected {story.id('Y')[:8]}" in text
    assert "cannot apply to the state produced by this reconciliation: signer is not a device" in text
    assert "decided." not in text  # the rejection line already names the signer


def test_rejected_operations_are_checked_independently(tmp_path):
    """Both concurrent additions work alone, but applying one makes the other fail."""
    genesis = encoding.make_genesis("00" * 32, [{"member_id": "alice", "name": "Alice"}],
                                    [{"device_key": KEY["a"], "member_id": "alice", "label": "a"}],
                                    time=GENESIS_TIME)
    origin = encoding.decode_genesis(genesis)["id"]
    payload = {"op": "add_device", "member_id": "alice", "device_key": KEY["b2"], "label": "new"}
    events = [encoding.make_event(SEEDS["a"], origin, "ordinary", origin, payload,
                                  time=GENESIS_TIME + n) for n in (1, 2)]
    ids = [encoding.decode_object(e)["id"] for e in events]
    r = encoding.make_event(SEEDS["a"], origin, "reconciliation", origin,
                            encoding.make_reconciliation_payload(ids, [], ids, events),
                            time=GENESIS_TIME + 3)
    r_id = encoding.decode_object(r)["id"]
    path = rewrite_bundle(origin, tmp_path, r_id, [genesis, r])
    code, result = verify_json(path, origin)
    assert (code, result["status"]) == (0, "valid")
    assert result["reconciliation"]["adopted"] == []
    assert result["conflict"]["rejected_after_kept"] == {ids[0]: None, ids[1]: None}
    assert tables(result["final_state"]) == {"Alice": ["a"]}
    done = verify(path, origin)
    assert done.returncode == 0
    for event in ids:
        assert f"rejected {event[:8]}. Its operation could still apply" in done.stdout
    for phrase in ["checked independently", "signer's reasons", "without its ancestors"]:
        assert phrase in done.stdout


def test_the_bundle_holds_no_private_keys(story):
    for encoded in bundle_objects(story).values():
        text = encoded.decode()
        for seed in SEEDS.values():
            assert seed.hex() not in text


def test_a_bundle_from_another_team_is_rejected(story, tmp_path):
    other = encoding.make_genesis("11" * 32, [{"member_id": "alice", "name": "Alice"}],
                                  [{"device_key": KEY["a"], "member_id": "alice", "label": "a"}],
                                  time=GENESIS_TIME)
    other_id = encoding.decode_genesis(other)["id"]
    path = tmp_path / "other.json"
    path.write_bytes(encoding.make_bundle(other_id, other_id, [other]))
    code, result = verify_json(path, story.origin)
    assert (code, result["status"], result["check"]) == (1, "invalid", "origin")


def test_a_bundle_without_p_is_incomplete_and_names_p(story, tmp_path):
    s = story
    objects = bundle_objects(s)
    del objects["P"]
    code, result = verify_json(rewrite_bundle(s.origin, tmp_path, s.id("c3"), objects.values()), s.origin)
    assert (code, result["status"]) == (1, "incomplete")
    assert [s.names[m] for m in result["missing"]] == ["P"]


def test_a_bundle_with_an_altered_copy_is_invalid_core(story, tmp_path):
    """Y's copy says b9 instead of b2, and Alice re-signs R, so R's own
    signature is good and only the copy fails."""
    s = story
    objects = bundle_objects(s)
    r = encoding.decode_canonical(objects["R"])
    copies = []
    for h in r["body"]["payload"]["copies"]:
        copy = encoding.decode_canonical(bytes.fromhex(h))
        if copy["body"]["payload"].get("label") == "b2":
            copy["body"]["payload"]["label"] = "b9"
        copies.append(encoding.canonical(copy).hex())
    r["body"]["payload"]["copies"] = copies
    r["sig"] = encoding.sign(SEEDS["a"], r["body"])
    altered_r = encoding.canonical(r)
    path = tmp_path / "altered.json"
    r_id = encoding.object_id(r["body"])
    by_id = {s.origin: objects["G"], s.id("P"): objects["P"], r_id: altered_r}
    path.write_bytes(encoding.canonical({
        "format": "spine-bundle-v1", "origin": s.origin, "tip": r_id,
        "objects": [by_id[i].hex() for i in sorted(by_id)],
    }))
    code, result = verify_json(path, s.origin)
    assert (code, result["status"], result["check"]) == (1, "invalid", "core")
    assert result["reason"] == "bundle does not decode: bad signature"
