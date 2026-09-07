"""Move 10 steps 3 and 4: representations measured against the obligations.

A standalone model, like ./model_human_resolution.py, whose vocabulary,
schedules and classification this file reuses rather than restates. It runs no
runtime code and accepts no representation; every candidate here is a modeling
object.

The obligations are ../obligations-actor-claims.md. The oracle is deliberately
not candidate-supplied: `classify`, `tips` and `pause_causes` come from step 2
and are the same for every candidate. A candidate decides only what records
exist and which actor holds which links, so a passing candidate passes because
of the evidence it puts in an actor's hands.

Candidates compared:

  - `MULTI_PARENT`: the resolution selection names both reviewed alternatives,
    and announcements publish those links.
  - `SEPARATE_RECORD`: the selection names only the chosen alternative, and a
    separate resolution record retires the other. Published alongside.
  - `RETAINED_LOCAL`: the same record, never published. Public succession stays
    a single-predecessor chain, which is the "derive it from retained private
    evidence" option.
  - The scalar counter is kept as an ordering baseline only, per Move 3.

The last two share one mechanism and differ only in whether the record is
published, which is itself a result: the choice between them is a disclosure
decision, not a representation decision.

Assumptions carried from step 2 apply unchanged, including that nothing here is
evidence that a person reviewed anything.
"""
import dataclasses
import itertools

import pytest

from model_human_resolution import (
    INCOMPATIBLE,
    INCOMPLETE,
    ROUTE_A,
    ROUTE_B,
    ROUTE_P,
    ROUTE_S,
    SUPERSESSION,
    Actor,
    Announcement,
    Selection,
    View,
    classify,
    historical_forks,
    missing_intermediate,
    missing_second_parent,
    pair_claims,
    selection,
    tips,
    unlinked,
)

# --- Ancestry that does not come from the selection record ------------------


@dataclasses.dataclass(frozen=True)
class ResolutionRecord:
    """A reviewed choice, recorded apart from the selection it applies to."""

    selection_id: str
    retires: frozenset


class LinkedView(View):
    """A view whose ancestry may also come from resolution records it holds.

    The extension is what lets one classification serve every candidate: where
    the links live is the candidate's business, and whether an actor holds them
    is what the comparison is about.
    """

    def __init__(self, *selections):
        super().__init__(*selections)
        self.side_links = {}

    def hold_resolution(self, record):
        self.side_links.setdefault(record.selection_id, set()).update(record.retires)
        return self

    def _parents(self, current):
        named = set() if current.parents is None else set(current.parents)
        return named | self.side_links.get(current.selection_id, set())

    def ancestors(self, selection_id):
        seen = set()
        frontier = [selection_id]
        while frontier:
            current = self.held.get(frontier.pop())
            if current is None:
                continue
            for parent in self._parents(current):
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        return seen

    def ancestry_complete(self, selection_id):
        """A resolution record says what was retired, not where a tip came from."""
        frontier = [selection_id]
        while frontier:
            current = self.held.get(frontier.pop())
            if current is None or current.parents is None:
                return False
            frontier.extend(self._parents(current))
        return True


def current_route(view):
    """The route an actor would use: the single tip, or nothing."""
    remaining = tips(view)
    if len(remaining) != 1:
        return None
    return view.get(next(iter(remaining))).route


# --- Candidates -------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Candidate:
    name: str
    links_in_selection: bool
    publish_record: bool


MULTI_PARENT = Candidate("multi-parent", True, False)
SEPARATE_RECORD = Candidate("separate-record", False, True)
RETAINED_LOCAL = Candidate("retained-local", False, False)
CANDIDATES = [MULTI_PARENT, SEPARATE_RECORD, RETAINED_LOCAL]


def actor(name, kind):
    a = Actor(name, kind)
    a.view = LinkedView()
    return a


def mint_resolution(candidate, resolver, chosen_id, other_id, resolution_id):
    """The records a resolution creates under one candidate.

    Nothing about the shape distinguishes a reviewed choice from an automatic
    one; that is asserted below rather than assumed away.
    """
    route = resolver.view.get(chosen_id).route
    if candidate.links_in_selection:
        made = selection(resolution_id, route, chosen_id, other_id)
        record = None
    else:
        made = selection(resolution_id, route, chosen_id)
        record = ResolutionRecord(resolution_id, frozenset({other_id}))
    resolver.view.receive(made)
    if record is not None:
        resolver.view.hold_resolution(record)
    return made, record


def deliver(candidate, teammate, resolver, selection_ids, record=None):
    for selection_id in selection_ids:
        teammate.deliver(resolver.announce(selection_id, publish_parents=True))
    if record is not None and candidate.publish_record:
        teammate.view.hold_resolution(record)


# --- Schedule: resolve one side of a fork, then rotate normally -------------


def _fork(resolver):
    resolver.select("sel-p", ROUTE_P)
    resolver.select("sel-a", ROUTE_A, "sel-p")
    resolver.select("sel-b", ROUTE_B, "sel-p")


def _resolved_and_rotated(candidate, chosen_id, other_id):
    resolver = actor("A", "device")
    _fork(resolver)
    _, record = mint_resolution(candidate, resolver, chosen_id, other_id, "sel-r")
    resolver.select("sel-s", ROUTE_S, "sel-r")
    return resolver, record


SIDES = [("sel-a", "sel-b"), ("sel-b", "sel-a")]
SIDE_IDS = ["chose_a", "chose_b"]


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("candidate", CANDIDATES, ids=lambda c: c.name)
def test_the_resolver_holds_no_current_disagreement_after_rotating(
    candidate, chosen_id, other_id
):
    """Every candidate repairs the first counterexample for the resolver."""
    resolver, _ = _resolved_and_rotated(candidate, chosen_id, other_id)
    assert tips(resolver.view) == {"sel-s"}
    assert not pair_claims(resolver.view), "nothing incompatible remains current"
    assert historical_forks(resolver.view) == {("sel-a", "sel-b")}
    assert {"sel-a", "sel-b", "sel-r"} <= set(resolver.view.held), (
        "both alternatives and the resolution are preserved, not deleted"
    )
    assert resolver.may_resume()


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
def test_which_predecessor_the_selection_names_stops_mattering(chosen_id, other_id):
    """A record holder is unaffected by the choice that broke the candidate."""
    resolver, record = _resolved_and_rotated(SEPARATE_RECORD, chosen_id, other_id)
    assert resolver.view.get("sel-r").parents == frozenset({chosen_id})
    assert record.retires == frozenset({other_id})
    assert other_id in resolver.view.ancestors("sel-s")


# --- Schedule: old selections delivered late and repeatedly to a teammate ----

LATE_ORDERS = list(itertools.permutations(["sel-a", "sel-b"]))


@pytest.mark.parametrize("order", LATE_ORDERS, ids=lambda o: "_".join(o))
@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("candidate", CANDIDATES, ids=lambda c: c.name)
def test_a_teammate_concludes_only_from_what_the_candidate_publishes(
    candidate, chosen_id, other_id, order
):
    """The comparison's discriminating case, stated as held evidence."""
    resolver, record = _resolved_and_rotated(candidate, chosen_id, other_id)
    teammate = actor("T", "teammate")
    deliver(candidate, teammate, resolver, ["sel-p", "sel-r", "sel-s"], record)
    deliver(candidate, teammate, resolver, order)
    deliver(candidate, teammate, resolver, order, record)

    if candidate is RETAINED_LOCAL:
        # The unmet obligation, preserved rather than argued around: public
        # succession alone never retires the alternative, so the teammate holds
        # two current tips and may claim neither a rotation nor a disagreement.
        assert tips(teammate.view) == {"sel-s", other_id}
        assert current_route(teammate.view) is None
        assert classify(teammate.view, "sel-s", other_id) == INCOMPATIBLE
        assert not teammate.may_resume()
    else:
        assert tips(teammate.view) == {"sel-s"}
        assert current_route(teammate.view) == ROUTE_S, (
            "the rotation after the resolution is the current route"
        )
        assert resolver.view.get("sel-r").route == resolver.view.get(chosen_id).route
        assert classify(teammate.view, "sel-s", other_id) == SUPERSESSION
        assert teammate.may_resume(), "repeated late delivery does not reopen it"


@pytest.mark.parametrize("candidate", [MULTI_PARENT, SEPARATE_RECORD],
                         ids=lambda c: c.name)
def test_repeated_delivery_changes_nothing(candidate):
    resolver, record = _resolved_and_rotated(candidate, "sel-b", "sel-a")
    teammate = actor("T", "teammate")
    deliver(candidate, teammate, resolver, ["sel-p", "sel-a", "sel-b", "sel-r", "sel-s"],
            record)
    settled = current_route(teammate.view)
    for _ in range(3):
        deliver(candidate, teammate, resolver, ["sel-a", "sel-b", "sel-r"], record)
        assert current_route(teammate.view) == settled == ROUTE_S


# --- Schedule: missing ancestry, in both arrival orders ---------------------


@pytest.mark.parametrize(
    "order", list(itertools.permutations(["sel-x", "sel-z"])),
    ids=lambda o: "_".join(o),
)
@pytest.mark.parametrize(
    "schedule", [missing_intermediate, missing_second_parent],
    ids=["missing_intermediate", "missing_second_parent"],
)
@pytest.mark.parametrize("candidate", CANDIDATES, ids=lambda c: c.name)
def test_missing_ancestry_is_uncertainty_under_every_candidate(
    candidate, schedule, order
):
    """No candidate turns a gap into a disagreement, in either arrival order."""
    parts = schedule()
    # Both schedules end (..., Y, Z), and Y is the link the recipient lacks.
    source, missing = parts[0], parts[-2]
    teammate = actor("T", "teammate")
    holder = actor("H", "device")
    holder.view = LinkedView(*source.held.values())
    for selection_id in order:
        teammate.deliver(holder.announce(selection_id, publish_parents=True))

    assert classify(teammate.view, "sel-x", "sel-z") == INCOMPLETE
    assert (INCOMPLETE, ("sel-x", "sel-z")) in teammate.pause_causes()
    assert current_route(teammate.view) is None

    teammate.view.receive(missing)
    assert classify(teammate.view, "sel-x", "sel-z") == SUPERSESSION
    assert teammate.may_resume(), "the arriving link cleared the uncertainty"


def test_arriving_evidence_can_confirm_a_disagreement_instead():
    """The same act of delivery, with the opposite conclusion."""
    teammate = actor("T", "teammate")
    teammate.view.receive(
        selection("sel-a", ROUTE_A, "sel-p"), selection("sel-b", ROUTE_B, "sel-p")
    )
    assert classify(teammate.view, "sel-a", "sel-b") == INCOMPLETE

    teammate.view.receive(selection("sel-p", ROUTE_P))
    assert classify(teammate.view, "sel-a", "sel-b") == INCOMPATIBLE, (
        "receiving the branch point settles it the other way"
    )
    assert not teammate.may_resume()


# --- The operation boundary -------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Refusal:
    reason: str
    report: dict


def apply_resolution(candidate, resolver, report, chosen_id, other_id, resolution_id):
    """Bind application to the reviewed evidence, or refuse.

    The binding is candidate-independent on purpose: naming the reviewed
    predecessors is not the same as checking that nothing else arrived.
    """
    if resolver.inspect() != report:
        return Refusal("evidence changed since the report", resolver.inspect())
    made, record = mint_resolution(
        candidate, resolver, chosen_id, other_id, resolution_id
    )
    resolver.publish(resolver.announce(made.selection_id, publish_parents=True))
    return made, record


@pytest.mark.parametrize("candidate", CANDIDATES, ids=lambda c: c.name)
def test_a_third_selection_between_report_and_application_refuses(candidate):
    resolver = actor("A", "device")
    _fork(resolver)
    report = resolver.inspect()
    assert report["claims"][("sel-a", "sel-b")] == INCOMPATIBLE

    resolver.select("sel-c", ROUTE_S, "sel-p")
    outcome = apply_resolution(candidate, resolver, report, "sel-b", "sel-a", "sel-r")

    assert isinstance(outcome, Refusal)
    assert "sel-r" not in resolver.view.held, "no selection was changed"
    assert not resolver.published, "and nothing was published"
    assert tips(resolver.view) == {"sel-a", "sel-b", "sel-c"}
    assert ("sel-a", "sel-c") in outcome.report["claims"], (
        "the new report carries the evidence that arrived"
    )
    assert set(outcome.report["tips"]) > set(report["tips"])


@pytest.mark.parametrize("candidate", CANDIDATES, ids=lambda c: c.name)
def test_unchanged_evidence_applies_and_clears_the_pause(candidate):
    resolver = actor("A", "device")
    _fork(resolver)
    report = resolver.inspect()
    outcome = apply_resolution(candidate, resolver, report, "sel-b", "sel-a", "sel-r")

    assert not isinstance(outcome, Refusal)
    assert tips(resolver.view) == {"sel-r"}
    assert resolver.published and resolver.may_resume()


def test_a_merge_shape_does_not_prove_human_review():
    """The distinction the public-payload controls established, carried forward."""
    reviewed = actor("A", "device")
    _fork(reviewed)
    mint_resolution(MULTI_PARENT, reviewed, "sel-b", "sel-a", "sel-r")

    automatic = actor("A", "device")
    _fork(automatic)
    automatic.view.receive(selection("sel-r", ROUTE_B, "sel-b", "sel-a"))

    assert reviewed.announce("sel-r", True) == automatic.announce("sel-r", True), (
        "a teammate holds identical evidence either way, so no report drawn "
        "from it may claim that a person reviewed the alternatives"
    )


# --- The counter, kept as an ordering baseline only -------------------------


def counter_route(ranked):
    """Move 3's baseline: greatest counter wins, and nothing ever pauses."""
    return max(ranked, key=lambda pair: pair[0])[1]


def test_the_counter_baseline_orders_without_detecting_anything():
    assert counter_route([(2, ROUTE_A), (2, ROUTE_B), (3, ROUTE_S)]) == ROUTE_S
    # Equal counters on an unresolved fork still yield a route, which is the
    # whole objection: the baseline cannot report what it cannot represent.
    assert counter_route([(2, ROUTE_A), (2, ROUTE_B)]) in (ROUTE_A, ROUTE_B)
    teammate = actor("T", "teammate")
    teammate.deliver(Announcement("A", "sel-a", ROUTE_A))
    teammate.deliver(Announcement("B", "sel-b", ROUTE_B))
    assert set(pair_claims(teammate.view).values()) == {INCOMPLETE}, (
        "the counter is not evidence of agreement, review, or ancestry"
    )
    assert teammate.view.get("sel-a") == unlinked("sel-a", ROUTE_A)


# ===========================================================================
# The discriminating experiment: partial delivery of selection and record
# ===========================================================================
#
# Run to make the step 5 checkpoint decidable, not to settle the
# representation. The question the comparison left open is whether the
# separate record's failure mode — a holder of the selection alone concluding a
# disagreement that was settled — can be turned into uncertainty that later
# evidence clears.
#
# The mechanism tested is the obvious one: the selection names the identity of
# its resolution record, and the record names what it retires. The record is
# then an ancestry node carrying no route, so its absence is a missing named
# parent, which step 2 already classifies as incomplete ancestry.


def resolution_node(record_id, retires):
    """A resolution record as an ancestry node with no route of its own."""
    return Selection(record_id, frozenset(retires), None)


def _resolved_with_referenced_record(chosen_id, other_id):
    resolver = actor("A", "device")
    _fork(resolver)
    node = resolution_node("rec-1", [other_id])
    resolver.view.receive(node, selection("sel-r", resolver.view.get(chosen_id).route,
                                          chosen_id, "rec-1"))
    return resolver, node


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
def test_a_referenced_record_turns_its_own_absence_into_uncertainty(
    chosen_id, other_id
):
    resolver, node = _resolved_with_referenced_record(chosen_id, other_id)
    teammate = actor("T", "teammate")
    for selection_id in ("sel-p", chosen_id, other_id, "sel-r"):
        teammate.deliver(resolver.announce(selection_id, publish_parents=True))

    assert classify(teammate.view, "sel-r", other_id) == INCOMPLETE, (
        "the named record is missing, so the teammate claims nothing"
    )
    assert not teammate.may_resume()

    teammate.view.receive(node)
    assert classify(teammate.view, "sel-r", other_id) == SUPERSESSION
    assert tips(teammate.view) == {"sel-r"}
    assert teammate.may_resume(), "the arriving record cleared the uncertainty"


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
def test_a_record_arriving_first_leaves_a_populated_view_undisturbed(
    chosen_id, other_id
):
    """The other delivery order, into a teammate that already holds history.

    The record retires a selection the teammate holds, and no selection yet
    references the record. It must sit inert rather than pose as a route.
    """
    resolver, node = _resolved_with_referenced_record(chosen_id, other_id)
    teammate = actor("T", "teammate")
    for selection_id in ("sel-p", chosen_id, other_id):
        teammate.deliver(resolver.announce(selection_id, publish_parents=True))
    before = pair_claims(teammate.view)

    teammate.view.receive(node)
    assert "rec-1" not in tips(teammate.view), "a routeless record is not a tip"
    assert tips(teammate.view) == {chosen_id}, (
        "the record retired only what it names"
    )
    assert pair_claims(teammate.view) == {}, (
        f"no new claim appeared; before the record the fork was {before}"
    )

    teammate.deliver(resolver.announce("sel-r", publish_parents=True))
    assert tips(teammate.view) == {"sel-r"}
    assert teammate.may_resume()


def test_a_record_arriving_first_does_not_pause_a_settled_lineage():
    """The review's counterexample: a clean A -> B history, record retiring A."""
    teammate = actor("T", "teammate")
    teammate.view.receive(selection("sel-a", ROUTE_A),
                          selection("sel-b", ROUTE_B, "sel-a"))
    teammate.view.receive(resolution_node("rec-1", ["sel-a"]))

    assert tips(teammate.view) == {"sel-b"}
    assert pair_claims(teammate.view) == {}
    assert teammate.may_resume()


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
def test_an_unreferenced_record_makes_its_own_absence_a_false_conflict(
    chosen_id, other_id
):
    """The failure the reference is meant to remove, preserved as a control."""
    resolver, record = _resolved_and_rotated(SEPARATE_RECORD, chosen_id, other_id)
    teammate = actor("T", "teammate")
    deliver(SEPARATE_RECORD, teammate, resolver,
            ["sel-p", chosen_id, other_id, "sel-r", "sel-s"])  # record withheld

    assert classify(teammate.view, "sel-s", other_id) == INCOMPATIBLE, (
        "nothing in the selection says a record was ever made, so a settled "
        "disagreement is indistinguishable from a live one"
    )


def test_a_record_arriving_before_its_selection_pauses_nothing():
    """Interruption in the other order: the record alone is inert."""
    resolver, node = _resolved_with_referenced_record("sel-b", "sel-a")
    teammate = actor("T", "teammate")
    teammate.view.receive(node)
    assert not teammate.pause_causes() and teammate.may_resume()

    for selection_id in ("sel-p", "sel-a", "sel-b", "sel-r"):
        teammate.deliver(resolver.announce(selection_id, publish_parents=True))
    assert tips(teammate.view) == {"sel-r"} and teammate.may_resume()


def test_multi_parent_has_no_partial_delivery_state():
    """The property the reference is buying back, stated for comparison."""
    resolver, _ = _resolved_and_rotated(MULTI_PARENT, "sel-b", "sel-a")
    teammate = actor("T", "teammate")
    deliver(MULTI_PARENT, teammate, resolver,
            ["sel-p", "sel-a", "sel-b", "sel-r", "sel-s"])
    assert tips(teammate.view) == {"sel-s"}
    assert resolver.view.get("sel-r").parents == frozenset({"sel-b", "sel-a"}), (
        "holding the selection is holding the retirement; there is nothing "
        "separate to lose or withhold"
    )


def test_the_reference_converts_a_false_conflict_but_buys_no_progress():
    """Withholding a referenced record leaves a pause that never clears."""
    resolver, _ = _resolved_with_referenced_record("sel-b", "sel-a")
    teammate = actor("T", "teammate")
    for selection_id in ("sel-p", "sel-a", "sel-b", "sel-r"):
        teammate.deliver(resolver.announce(selection_id, publish_parents=True))
    for _ in range(3):
        teammate.deliver(resolver.announce("sel-r", publish_parents=True))
        assert classify(teammate.view, "sel-r", "sel-a") == INCOMPLETE
        assert current_route(teammate.view) is None
    # Better than a false conflict, and still not the retained-local candidate
    # rescued: the teammate waits on evidence that a policy decision withholds.
