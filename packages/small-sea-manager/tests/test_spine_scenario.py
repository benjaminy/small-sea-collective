"""The slice 1 story, with literal views of a, b, and c after each step.

The scenario only publishes and delivers; every expectation below is computed by
the devices themselves. Object IDs are translated to story names for reading.
"""

import pytest

from small_sea_manager.spine.device import Refused
from small_sea_manager.spine.scenario import Scenario

ALL = ["ordinary", "reconciliation"]
RESOLVING = ["reconciliation"]
OK = ("valid", None)
P_TABLES = {"alice": ["a"], "bob": ["b"], "carol": ["c", "c2"]}
X_TABLES = {"alice": ["a"], "carol": ["c", "c2"]}
Y_TABLES = {"alice": ["a"], "bob": ["b", "b2"], "carol": ["c", "c2"]}


def tables(state):
    out = {member: [] for member in state["members"]}
    for row in state["devices"].values():
        out[row["member_id"]].append(row["label"])
    return {member: sorted(labels) for member, labels in out.items()}


def views(s):
    def one(device):
        v = device.view()
        n = lambda ids: sorted(s.names[i] for i in ids)
        return {
            "objects": {s.names[i]: (d["status"], d["reason"]) for i, d in v["objects"].items()},
            "heads": n(v["heads"]),
            "classification": v["classification"],
            "tables": tables(v["acting_state"]),
            "may_publish": v["may_publish"],
            "contested": n(v["contested"]),
        }
    return {label: one(device) for label, device in s.devices.items()}


def followed(objects, head, tables_):
    return {"objects": objects, "heads": [head], "classification": "followed",
            "tables": tables_, "may_publish": ALL, "contested": []}


def conflict(objects):
    return {"objects": objects, "heads": ["X", "Y"], "classification": "conflict",
            "tables": P_TABLES, "may_publish": RESOLVING, "contested": []}


ALL_THREE = {"P": OK, "X": OK, "Y": OK}
WITH_R = dict(ALL_THREE, R=OK)
AFTER = {
    "a": followed(dict(WITH_R, c3=OK), "c3", {"alice": ["a"], "carol": ["c", "c2", "c3"]}),
    "b": conflict(dict(ALL_THREE, R=("invalid", "refused by this device's owner"))),
    "c": followed(dict(WITH_R, c3=OK), "c3", {"alice": ["a"], "carol": ["c", "c2", "c3"]}),
}


def test_the_story(tmp_path):
    s = Scenario(tmp_path)

    s.step1_carol_adds_phone()
    assert views(s) == {d: followed({"P": OK}, "P", P_TABLES) for d in "abc"}

    s.step2_alice_removes_bob_and_bob_adds_device()
    assert views(s) == {
        "a": followed({"P": OK, "X": OK}, "X", X_TABLES),
        "b": followed({"P": OK, "Y": OK}, "Y", Y_TABLES),
        "c": followed({"P": OK}, "P", P_TABLES),
    }

    s.step3a_carol_gets_bobs_device()
    assert views(s)["c"] == followed({"P": OK, "Y": OK}, "Y", Y_TABLES)

    s.step3b_everyone_sees_both()
    assert views(s) == {d: conflict(ALL_THREE) for d in "abc"}
    assert [r["disposition"] for r in s.c.receipts()][-2:] == ["valid", "duplicate"]

    with pytest.raises(Refused, match="paused: conflict"):
        s.step4_carol_tries_tablet()
    assert views(s) == {d: conflict(ALL_THREE) for d in "abc"}

    s.step5_alice_reconciles()
    assert views(s) == {
        "a": followed(WITH_R, "R", X_TABLES),
        "b": conflict(ALL_THREE),
        "c": conflict(ALL_THREE),
    }

    s.step6a_bob_and_carol_get_r()
    must_choose = {"objects": WITH_R, "heads": ["R"], "classification": "owner must choose",
                   "tables": Y_TABLES, "may_publish": RESOLVING, "contested": ["R"]}
    assert views(s)["b"] == views(s)["c"] == must_choose

    s.step6b_carol_accepts_bob_refuses()
    assert views(s) == {
        "a": followed(WITH_R, "R", X_TABLES),
        "b": conflict(dict(ALL_THREE, R=("invalid", "refused by this device's owner"))),
        "c": followed(WITH_R, "R", X_TABLES),
    }

    s.step7_carol_adds_tablet()
    assert views(s) == AFTER


def test_the_story_without_looking_between_steps(tmp_path):
    """Asking for a view records the followed head, so the story must also
    work when nobody looks until the end."""
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
    assert views(s) == AFTER
