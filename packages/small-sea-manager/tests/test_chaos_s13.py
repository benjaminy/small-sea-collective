"""Chaos scenario S13: several refs at one SHA do not create duplicate work.

Three parked refs point at the same source commit: the fixture's ref, an
observation ref for that same logical teammate, and a ref for a different
teammate. All three must stay visible and maximal, exactly one first-sweep
result reports the event as new, and the second sweep changes nothing.
"""

import sqlite3

from cod_sync.repo import Repo
from small_sea_manager.manager import TeamManager
from test_core_union import _fixture


def _event_rows(sync) -> list:
    with sqlite3.connect(sync / "core.db") as conn:
        return conn.execute(
            "SELECT event_id,event_type,encoded FROM constitution_event ORDER BY event_id"
        ).fetchall()


def test_equal_sha_refs_are_maximal_and_integrated_once(playground_dir):
    root, alice_hex, sync, repo, sha, ref, event = _fixture(playground_dir)
    # One observation ref for the same logical teammate as the fixture ref,
    # and one latest ref for a different teammate, both at the same SHA.
    teammate_a = ref.removeprefix("refs/small-sea/core-peer/").split("/")[0]
    ref_obs = f"refs/small-sea/core-peer/{teammate_a}/observations/s13-a"
    ref_other = "refs/small-sea/core-peer/" + "cd" * 32 + "/latest"
    repo.create_ref_immutable(ref_obs, sha)
    repo.create_ref_immutable(ref_other, sha)

    tm = TeamManager(root, alice_hex)

    # All three refs remain visible and maximal at their original SHA.
    heads = tm.list_core_source_heads("ProjectX")
    assert {(h.ref_name, h.head_sha) for h in heads} == {(ref, sha), (ref_obs, sha), (ref_other, sha)}
    assert all(h.is_maximal for h in heads)
    assert all(h.head_sha == sha for h in heads)

    first = tm.integrate_core_sources("ProjectX")
    assert len(first) == 3
    new = [r for r in first if r["new_events"] == 1]
    none = [r for r in first if r["outcome"] == "no_change" and r["new_events"] == 0]
    # Exactly one first-sweep result reports the fixture event as new.
    assert len(new) == 1, first
    assert new[0]["outcome"] == "integrated"
    assert {r["ref_name"] for r in none} == {ref_obs, ref_other}

    # The second sweep changes neither rows nor HEAD.
    rows_before = _event_rows(sync)
    head_before = repo.head()
    second = tm.integrate_core_sources("ProjectX")
    assert len(second) == 3
    assert all(r["outcome"] == "no_change" and r["new_events"] == 0 for r in second), second
    assert _event_rows(sync) == rows_before
    assert repo.head() == head_before

    # All refs still point to their original SHA.
    refs = repo.list_refs("refs/small-sea/core-peer")
    assert {ref, ref_obs, ref_other} <= set(refs)
    assert all(refs[r] == sha for r in (ref, ref_obs, ref_other))
