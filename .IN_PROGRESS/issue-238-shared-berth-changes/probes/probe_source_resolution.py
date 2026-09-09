"""Step 5 validation: the whole human-resolution path against the runtime.

Unlike the Move 7-10 probes in this directory, this file is not asserting a
defect. It runs the implemented path end to end -- real Manager, real Hub, real
MinIO, two installations -- and each test is one row of plan.md's step 5 table:

  * competing choices become a visible pause, and unrelated work survives
  * both candidates stay inspectable while the berth is paused
  * an unreachable candidate is reported without resuming anything
  * evidence that explains the disagreement away does not release the pause
  * either existing location can be chosen, and publication resumes there
  * a choice reviewed before an investigation is refused as stale
  * a candidate a sibling deleted is still inspectable and still choosable

The schedule is the Move 8 wedge with both locations actually written, so
inspection has something to find at each. The two-installation setup and the
device-switching helpers are Move 7's and Move 8's, imported rather than
copied.

What this file does not cover, and what therefore has no runtime evidence yet:
interrupting adoption between the shared rows and the projected pause,
racing a locator writeback or a new candidate against a resolution's own
transaction, and retrying a publication interrupted after resolution.
"""
import pathlib

import pytest
from cod_sync.protocol import PublicationIntegrationRequiredError
from small_sea_client.client import SmallSeaCloudStorageRequired
from small_sea_manager import provisioning
from small_sea_note_to_self.db import attached_note_to_self_connection

from probe_interrupted_finalization import TEAM, _core_allocation, _two_installations
from probe_multiple_locations import _redistribute_b_sender_key
from probe_publication_adoption import _manager_b, _reattach

MAIN = "refs/heads/main"


def _live_locations(root, alice_hex, berth_id):
    with attached_note_to_self_connection(root, alice_hex) as conn:
        return sorted(
            row[0]
            for row in conn.execute(
                "SELECT location FROM berth_cloud_allocation WHERE berth_id = ?",
                (berth_id,),
            )
        )


def _key_for(status, location):
    [candidate] = [c for c in status["candidates"] if c["location"] == location]
    return candidate["candidate_key"]


def _candidate(status, location):
    [candidate] = [c for c in status["candidates"] if c["location"] == location]
    return candidate


def _paused_with_two_written_locations(workspace, minio):
    """The Move 8 wedge, with each sibling's chain actually published.

    A and B fork from a common base, each rotates the Core berth and publishes
    its team chain to its own new bucket, and A also registers an unrelated
    cloud account so the run can say whether work nobody disagreed about
    survives. B then adopts A's NoteToSelf head and ends holding the pause.

    B's sender key goes to A before B's first upload, for the reason the
    multiple-location probe established: a distribution carries the chain at
    its current iteration and none of the earlier message keys.
    """
    setup = _two_installations(workspace, minio, {})
    root_a, root_b, alice_hex = setup["root_a"], setup["root_b"], setup["alice_hex"]
    manager_a = setup["manager_a"]

    manager_a.push_team(TEAM)
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()
    manager_b.reconcile_team_route(TEAM, new_location=True)
    _redistribute_b_sender_key(root_a, root_b, alice_hex)
    manager_b.push_team(TEAM)
    _, allocation_b = _core_allocation(root_b, alice_hex)
    head_b = manager_b._team_repo(TEAM).resolve_ref(MAIN)
    # Team work created before the fork and left unpublished, so a later
    # `push_team` has something real to publish and reaches the Hub instead of
    # reporting `already_present` from its own marker.
    manager_b.create_invitation(TEAM, invitee_label="carol")

    manager_a = _reattach(root_a, alice_hex)
    manager_a.reconcile_team_route(TEAM, new_location=True)
    manager_a.push_team(TEAM)
    _, allocation_a = _core_allocation(root_a, alice_hex)
    head_a = manager_a._team_repo(TEAM).resolve_ref(MAIN)
    manager_a.add_cloud_storage(protocol="s3", url=minio["endpoint"] + "/unrelated-a")
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    with pytest.raises(PublicationIntegrationRequiredError):
        manager_b.push_note_to_self()
    result = manager_b.refresh_note_to_self()
    assert result["integration"].outcome == "integrated", result["integration"]

    assert head_a != head_b
    assert manager_b._team_repo(TEAM).resolve_ref(MAIN) != head_b, (
        "B has no unpublished team work, so push_team would not reach the Hub"
    )
    return {
        **setup,
        "manager_a": manager_a,
        "manager_b": manager_b,
        "allocation_a": allocation_a,
        "allocation_b": allocation_b,
        "head_a": head_a,
        "head_b": head_b,
    }


def test_competing_choices_pause_the_berth_and_spare_unrelated_work(
    playground_dir, minio_server_gen
):
    """The disagreement is visible, the berth stops, and nothing else does.

    This is the whole point of removing allocation uniqueness. Under the old
    refusal B learned only that a commit would not apply, A's unrelated cloud
    account could not reach B at all, and B's own publication was blocked in
    both directions. Now both rows are adopted, A's account arrives with them,
    NoteToSelf keeps moving -- it is the channel the resolution travels over --
    and what stops is exactly the berth whose placement is in question.
    """
    setup = _paused_with_two_written_locations(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    root_b, alice_hex, berth_id = setup["root_b"], setup["alice_hex"], setup["berth_id"]
    manager_b = setup["manager_b"]

    status = manager_b.berth_source_status(TEAM)
    assert status["paused"] is True
    assert "berth_source_paused" in status["blocked"]
    assert sorted(c["location"] for c in status["candidates"] if c["live"]) == sorted(
        [setup["allocation_a"]["location"], setup["allocation_b"]["location"]]
    )
    assert status["unavailable"]["authorship"]

    # A's unrelated account rode along with the disagreement.
    assert any(
        s["url"].endswith("/unrelated-a") for s in manager_b.list_cloud_storage()
    )

    # The berth's own operations stop. Every own-berth provider call resolves
    # its location at one place in the Hub, so asserting it there is the
    # general claim; publishing genuinely outstanding team work is the same
    # refusal reached the way a person would reach it.
    session = manager_b._get_or_open_session(TEAM)
    with pytest.raises(SmallSeaCloudStorageRequired) as raised:
        session.ensure_cloud_ready()
    assert raised.value.reason == "berth_source_paused"

    with pytest.raises(SmallSeaCloudStorageRequired) as raised:
        manager_b.push_team(TEAM)
    assert raised.value.reason == "berth_source_paused"
    assert manager_b.core_storage_allocation(TEAM)["placement"] == "paused"
    assert manager_b.core_storage_allocation(TEAM)["allocation"] is None

    # NoteToSelf is not paused: resolution has to be able to travel.
    manager_b.push_note_to_self()
    assert _live_locations(root_b, alice_hex, berth_id) == sorted(
        [setup["allocation_a"]["location"], setup["allocation_b"]["location"]]
    )


def test_both_candidates_are_inspectable_while_the_berth_is_paused(
    playground_dir, minio_server_gen
):
    """A person can look at both locations without either becoming live.

    Each candidate is fetched under its own ref, the observations are retained
    against the candidate that produced them, and `main` does not move: getting
    the bytes back is not integration. The sibling's candidate has no
    announcement on this device -- A's announcement travels in a team chain
    published to the very bucket B cannot use yet -- and that is recorded as
    evidence rather than refusing the read.
    """
    setup = _paused_with_two_written_locations(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    manager_b = setup["manager_b"]
    status = manager_b.berth_source_status(TEAM)
    repo_b = manager_b._team_repo(TEAM)
    main_before = repo_b.resolve_ref(MAIN)

    for location in (setup["allocation_a"]["location"], setup["allocation_b"]["location"]):
        outcome = manager_b.inspect_berth_source_candidate(
            TEAM, _key_for(status, location)
        )
        assert outcome["observation"]["reached"] is True, outcome
        assert outcome["retained"] is True

    assert repo_b.resolve_ref(MAIN) == main_before
    after = manager_b.berth_source_status(TEAM)
    observed = {
        c["location"]: [o.get("observed_head") for o in c["observations"]]
        for c in after["candidates"]
    }
    assert observed[setup["allocation_a"]["location"]] == [setup["head_a"]]
    assert observed[setup["allocation_b"]["location"]] == [setup["head_b"]]
    assert (
        _candidate(after, setup["allocation_b"]["location"])["observations"][0][
            "announcement"
        ]
        == "announced"
    )
    assert (
        _candidate(after, setup["allocation_a"]["location"])["observations"][0][
            "announcement"
        ]
        == "missing"
    )
    assert after["paused"] is True


def test_an_unreachable_candidate_is_reported_and_resumes_nothing(
    playground_dir, minio_server_gen
):
    """Success at one location is not permission to carry on at the other.

    The account's credentials are replaced with wrong ones, so the provider
    itself refuses and the account stays registered.
    The failure is recorded as missing evidence a person may still decide
    without, and the pause is exactly as held as it was before.
    """
    minio = minio_server_gen()
    setup = _paused_with_two_written_locations(pathlib.Path(playground_dir), minio)
    manager_b = setup["manager_b"]
    status = manager_b.berth_source_status(TEAM)

    [account] = [
        s for s in manager_b.list_cloud_storage() if s["url"] == minio["endpoint"]
    ]
    manager_b.connect_cloud_storage_credentials(
        account["id"], access_key="wrong-key", secret_key="wrong-secret"
    )

    outcome = manager_b.inspect_berth_source_candidate(
        TEAM, _key_for(status, setup["allocation_a"]["location"])
    )
    assert outcome["observation"]["reached"] is False, outcome

    after = manager_b.berth_source_status(TEAM)
    assert after["paused"] is True
    assert _key_for(status, setup["allocation_a"]["location"]) in (
        after["unavailable"]["unreachable_candidates"]
    )


def test_evidence_that_explains_the_disagreement_away_keeps_the_pause(
    playground_dir, minio_server_gen
):
    """A sibling withdrawing its row is new evidence, not a decision.

    A resolves on its own side and publishes the deletion. B adopts it, so B's
    live state is once again a single allocation -- and B stays paused, with
    A's withdrawn location retained for inspection and choice. A predicate over
    current state would have released here.
    """
    setup = _paused_with_two_written_locations(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    root_a, root_b, alice_hex, berth_id = (
        setup["root_a"], setup["root_b"], setup["alice_hex"], setup["berth_id"]
    )

    _manager_b(root_b, alice_hex).push_note_to_self()
    manager_a = _reattach(root_a, alice_hex)
    manager_a.refresh_note_to_self()
    status_a = manager_a.berth_source_status(TEAM)
    assert status_a["paused"] is True
    manager_a.resolve_berth_source(
        TEAM,
        _key_for(status_a, setup["allocation_a"]["location"]),
        status_a["evidence_digest"],
    )
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()
    status_b = manager_b.berth_source_status(TEAM)

    assert _live_locations(root_b, alice_hex, berth_id) == [
        setup["allocation_a"]["location"]
    ]
    assert status_b["paused"] is True
    assert _candidate(status_b, setup["allocation_b"]["location"])["live"] is False
    with pytest.raises(SmallSeaCloudStorageRequired) as raised:
        manager_b.push_team(TEAM)
    assert raised.value.reason == "berth_source_paused"


@pytest.mark.parametrize("choose", ["a", "b"])
def test_either_existing_location_can_be_chosen_and_publication_resumes(
    playground_dir, minio_server_gen, choose
):
    """The Move 9 gap closes in both directions.

    Choosing the sibling's location and choosing this device's own are the same
    operation with a different candidate key: no raw row delete, no third
    rotation, and no location parameter on `reconcile_team_route`. Adopting a
    row is not the whole choice -- this device's own signed announcement may
    still name what it gave up -- so an argument-free reconcile is the second
    half, and then the berth publishes again.
    """
    setup = _paused_with_two_written_locations(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    root_b, alice_hex, berth_id = setup["root_b"], setup["alice_hex"], setup["berth_id"]
    manager_b = setup["manager_b"]
    chosen = setup["allocation_a" if choose == "a" else "allocation_b"]["location"]
    other = setup["allocation_b" if choose == "a" else "allocation_a"]["location"]

    status = manager_b.berth_source_status(TEAM)
    result = manager_b.resolve_berth_source(
        TEAM, _key_for(status, chosen), status["evidence_digest"]
    )

    assert result["resolved"] is True
    assert result["status"]["paused"] is False
    assert _live_locations(root_b, alice_hex, berth_id) == [chosen]
    # The alternative is withdrawn from shared state, not forgotten.
    assert _candidate(result["status"], other)["live"] is False
    assert any(
        s["url"].endswith("/unrelated-a") for s in manager_b.list_cloud_storage()
    )

    assert manager_b.reconcile_team_route(TEAM)["route"] == "ready"
    assert _live_locations(root_b, alice_hex, berth_id) == [chosen], (
        "reconcile rotated instead of signing for the chosen row"
    )
    manager_b.push_note_to_self()

    # Publication is attempted at the chosen location, and what happens there
    # is a question about the chain, not about the placement. B's own location
    # accepts its head; A's location already holds A's divergent Core chain, so
    # Cod Sync parks it for the ordinary integration path. Resolving where to
    # publish deliberately does not decide what to publish.
    if choose == "b":
        assert manager_b.push_team(TEAM) == "published"
    else:
        with pytest.raises(PublicationIntegrationRequiredError) as raised:
            manager_b.push_team(TEAM)
        assert raised.value.observed_head == setup["head_a"]


def test_a_choice_reviewed_before_an_investigation_is_refused(
    playground_dir, minio_server_gen
):
    """An observation changes the question, so the earlier review is stale.

    Not a technicality: the person is about to delete one of two locations, and
    what they read is what they are entitled to have decided over. The refusal
    hands back the updated report and leaves the pause exactly as it was.
    """
    setup = _paused_with_two_written_locations(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    root_b, alice_hex, berth_id = setup["root_b"], setup["alice_hex"], setup["berth_id"]
    manager_b = setup["manager_b"]

    reviewed = manager_b.berth_source_status(TEAM)
    manager_b.inspect_berth_source_candidate(
        TEAM, _key_for(reviewed, setup["allocation_a"]["location"])
    )

    result = manager_b.resolve_berth_source(
        TEAM,
        _key_for(reviewed, setup["allocation_a"]["location"]),
        reviewed["evidence_digest"],
    )

    assert result == {**result, "resolved": False, "reason": "evidence_changed"}
    assert result["status"]["paused"] is True
    assert result["status"]["evidence_digest"] != reviewed["evidence_digest"]
    assert len(_live_locations(root_b, alice_hex, berth_id)) == 2

    # Deciding over what the report now says works.
    fresh = result["status"]
    assert manager_b.resolve_berth_source(
        TEAM,
        _key_for(fresh, setup["allocation_a"]["location"]),
        fresh["evidence_digest"],
    )["resolved"] is True


def test_a_candidate_a_sibling_deleted_stays_inspectable_and_choosable(
    playground_dir, minio_server_gen
):
    """Shared deletion does not reach the paused device's own decision.

    A resolves for its own location and publishes the deletion of B's row. B
    adopts it, and can still read B's location through the Hub -- the
    investigation path bypasses the live-row lookup as well as the pause -- and
    can still choose it, which restores exactly the reviewed row.
    """
    setup = _paused_with_two_written_locations(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    root_a, root_b, alice_hex, berth_id = (
        setup["root_a"], setup["root_b"], setup["alice_hex"], setup["berth_id"]
    )

    _manager_b(root_b, alice_hex).push_note_to_self()
    manager_a = _reattach(root_a, alice_hex)
    manager_a.refresh_note_to_self()
    status_a = manager_a.berth_source_status(TEAM)
    assert status_a["paused"] is True
    manager_a.resolve_berth_source(
        TEAM,
        _key_for(status_a, setup["allocation_a"]["location"]),
        status_a["evidence_digest"],
    )
    manager_a.push_note_to_self()

    manager_b = _manager_b(root_b, alice_hex)
    manager_b.refresh_note_to_self()
    status_b = manager_b.berth_source_status(TEAM)
    withdrawn = _key_for(status_b, setup["allocation_b"]["location"])
    assert _candidate(status_b, setup["allocation_b"]["location"])["live"] is False

    outcome = manager_b.inspect_berth_source_candidate(TEAM, withdrawn)
    assert outcome["observation"]["reached"] is True, outcome
    assert outcome["observation"]["observed_head"] == setup["head_b"]

    status_b = manager_b.berth_source_status(TEAM)
    result = manager_b.resolve_berth_source(
        TEAM, withdrawn, status_b["evidence_digest"]
    )

    assert result["resolved"] is True, result
    assert _live_locations(root_b, alice_hex, berth_id) == [
        setup["allocation_b"]["location"]
    ]
    restored = provisioning.get_berth_cloud_allocation_for_berth(
        root_b, alice_hex, berth_id
    )
    assert restored["id"] == setup["allocation_b"]["id"], "a new allocation was minted"


def test_a_paused_device_cannot_rotate_its_way_out(
    playground_dir, minio_server_gen
):
    """Rotation states a preference; it is not a way to delete a rival row."""
    setup = _paused_with_two_written_locations(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    root_b, alice_hex, berth_id = setup["root_b"], setup["alice_hex"], setup["berth_id"]
    manager_b = setup["manager_b"]

    report = manager_b.reconcile_team_route(TEAM, new_location=True)

    assert report["route_reason"] == "berth_source_paused"
    assert len(_live_locations(root_b, alice_hex, berth_id)) == 2
    assert manager_b.berth_source_status(TEAM)["paused"] is True
