"""Move 10 checkpoint: when a resolution record's retirement takes effect.

A standalone model, like ./model_resolution_candidates.py, whose vocabulary,
schedules and classification it reuses rather than restates. It runs no runtime
code and accepts no representation.

The discriminating experiment left the separate record with a reference as the
better candidate, but reviewing it showed that receiving the record alone still
retires a branch and can clear a teammate's pause before the selection that
references it arrives. See ../notes.md#checkpoint-follow-up-2026-09-07-when-
retirement-takes-effect. That is the question here, and it is a semantic choice
about the record, not a defect in the classifier.

Two retirement semantics are compared, with multi-parent selections kept as the
control that has no separate artifact to time:

  - `INDEPENDENT`: what the model does today. A held record retires the
    selections it names, whether or not any held selection references it.
  - `GATED`: a held record supplies ancestry only through a held route
    selection that names it as a parent. An unreferenced record is inert: it is
    retained evidence and nothing else.

Both leave `classify`, `tips`, `pause_causes` and the resume rule from step 2
untouched; they differ only in when the record's links join the view's ancestry.
Every expectation below is written as literal tips, route, pause causes and
permission to resume, so that a candidate cannot pass by agreeing with the
classifier about what it just did.

Assumptions carried from step 2 and step 3 apply unchanged. Nothing here is
evidence that a person reviewed anything, and no artifact is withheld or sent
by any rule the model represents; withholding is a schedule, not a policy.
"""
import pytest

from model_human_resolution import (
    INCOMPATIBLE,
    INCOMPLETE,
    ROUTE_A,
    ROUTE_B,
    ROUTE_P,
    ROUTE_S,
    Actor,
    Selection,
    View,
    historical_forks,
    selection,
    tips,
)
from model_resolution_candidates import current_route

# --- The two retirement semantics -------------------------------------------


class IndependentView(View):
    """Today's semantics: holding the record is enough to retire what it names."""


class GatedView(View):
    """A record's links count only while a held *route selection* names it.

    The gate is on the record node, not on the classification: a referenced
    record contributes exactly the ancestry it always did, and an unreferenced
    one contributes none, so its arrival cannot change any tip.

    Activation is rooted in a route selection, and is not transitive: a record
    naming another record activates nothing, because a routeless node's own
    links are suppressed before they are read. Review of `a71d129` found that
    gathering references from every held node let two orphan records open each
    other's gate and retire a live branch with no route selection referencing
    either. Record-to-record links are therefore inert here rather than
    rejected; the shape is still received, retained and inspectable, and
    whether the vocabulary should carry it at all is a semantic question for
    the checkpoint. See ../notes.md#review-of-a71d129-2026-09-07.
    """

    def _referenced(self):
        named = set()
        for held in self.held.values():
            if held.route is None:
                continue
            named |= set(held.parents or ())
        return named

    def _links(self, current):
        if current.route is None and current.selection_id not in self._referenced():
            return frozenset()
        return current.parents

    def ancestors(self, selection_id):
        seen = set()
        frontier = [selection_id]
        while frontier:
            current = self.held.get(frontier.pop())
            if current is None:
                continue
            for parent in self._links(current) or ():
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        return seen

    def ancestry_complete(self, selection_id):
        frontier = [selection_id]
        while frontier:
            current = self.held.get(frontier.pop())
            if current is None:
                return False
            links = self._links(current)
            if links is None:
                return False
            frontier.extend(links)
        return True


SEMANTICS = [IndependentView, GatedView]
SEMANTICS_IDS = ["independent", "gated"]


def actor(name, kind, view_class):
    a = Actor(name, kind)
    a.view = view_class()
    return a


def resolution_node(record_id, retires):
    """A resolution record as an ancestry node with no route of its own."""
    return Selection(record_id, frozenset(retires), None)


def state(a):
    """What an actor would do next, stated without consulting a candidate."""
    return {
        "tips": frozenset(tips(a.view)),
        "route": current_route(a.view),
        "causes": frozenset(a.pause_causes()),
        "may_resume": a.may_resume(),
    }


def settled_on(selection_id, route):
    return {
        "tips": frozenset({selection_id}),
        "route": route,
        "causes": frozenset(),
        "may_resume": True,
    }


# --- Schedules ---------------------------------------------------------------

SIDES = [("sel-a", "sel-b"), ("sel-b", "sel-a")]
SIDE_IDS = ["chose_a", "chose_b"]

FORK_PAUSE = {
    "tips": frozenset({"sel-a", "sel-b"}),
    "route": None,
    "causes": frozenset({(INCOMPATIBLE, ("sel-a", "sel-b"))}),
    "may_resume": False,
}


def _resolver(view_class, chosen_id, other_id):
    """A device that forks, then resolves with a record and a reference to it."""
    resolver = actor("A", "device", view_class)
    resolver.select("sel-p", ROUTE_P)
    resolver.select("sel-a", ROUTE_A, "sel-p")
    resolver.select("sel-b", ROUTE_B, "sel-p")
    resolver.view.receive(resolution_node("rec-1", [other_id]))
    resolver.select("sel-r", resolver.view.get(chosen_id).route, chosen_id, "rec-1")
    return resolver


def _teammate_holding_the_fork(view_class, resolver):
    teammate = actor("T", "teammate", view_class)
    for selection_id in ("sel-p", "sel-a", "sel-b"):
        teammate.deliver(resolver.announce(selection_id, publish_parents=True))
    return teammate


def _pause_on_the_missing_record(other_id):
    return {
        "tips": frozenset({other_id, "sel-r"}),
        "route": None,
        "causes": frozenset({(INCOMPLETE, (other_id, "sel-r"))}),
        "may_resume": False,
    }


def _alternatives_retained(a):
    return {"sel-a", "sel-b"} <= set(a.view.held)


# --- The record arrives first, into a populated fork -------------------------


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_the_record_alone_decides_whether_a_paused_fork_resumes(
    view_class, chosen_id, other_id
):
    """The checkpoint's question, as the difference between two expectations."""
    resolver = _resolver(view_class, chosen_id, other_id)
    teammate = _teammate_holding_the_fork(view_class, resolver)
    assert state(teammate) == FORK_PAUSE, "the fork is a live disagreement"

    teammate.deliver(resolver.announce("rec-1", publish_parents=True))
    if view_class is IndependentView:
        assert state(teammate) == settled_on(
            chosen_id, resolver.view.get(chosen_id).route
        ), "retirement completed without the selection it explains"
    else:
        assert state(teammate) == FORK_PAUSE, (
            "an unreferenced record is retained evidence and changes nothing"
        )
    assert _alternatives_retained(teammate)


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_both_semantics_agree_once_the_referencing_selection_arrives(
    view_class, chosen_id, other_id
):
    resolver = _resolver(view_class, chosen_id, other_id)
    teammate = _teammate_holding_the_fork(view_class, resolver)
    teammate.deliver(resolver.announce("rec-1", publish_parents=True))
    teammate.deliver(resolver.announce("sel-r", publish_parents=True))

    assert state(teammate) == settled_on("sel-r", resolver.view.get("sel-r").route)
    assert _alternatives_retained(teammate)


# --- The selection arrives first ---------------------------------------------


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_the_selection_alone_is_uncertainty_under_both_semantics(
    view_class, chosen_id, other_id
):
    """Gating changes nothing here: the record is not held, so it cannot count."""
    resolver = _resolver(view_class, chosen_id, other_id)
    teammate = _teammate_holding_the_fork(view_class, resolver)
    teammate.deliver(resolver.announce("sel-r", publish_parents=True))
    assert state(teammate) == _pause_on_the_missing_record(other_id)

    teammate.deliver(resolver.announce("rec-1", publish_parents=True))
    assert state(teammate) == settled_on("sel-r", resolver.view.get("sel-r").route)
    assert _alternatives_retained(teammate)


# --- Permanent withholding of either artifact --------------------------------


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_withholding_the_record_pauses_indefinitely_under_both_semantics(
    view_class, chosen_id, other_id
):
    resolver = _resolver(view_class, chosen_id, other_id)
    teammate = _teammate_holding_the_fork(view_class, resolver)
    for _ in range(3):
        teammate.deliver(resolver.announce("sel-r", publish_parents=True))
        assert state(teammate) == _pause_on_the_missing_record(other_id)
    assert _alternatives_retained(teammate)


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_withholding_the_selection_is_where_the_semantics_disagree(
    view_class, chosen_id, other_id
):
    """The discriminating schedule: the record arrives and its selection never does."""
    resolver = _resolver(view_class, chosen_id, other_id)
    teammate = _teammate_holding_the_fork(view_class, resolver)
    for _ in range(3):
        teammate.deliver(resolver.announce("rec-1", publish_parents=True))
        if view_class is IndependentView:
            assert state(teammate) == settled_on(
                chosen_id, resolver.view.get(chosen_id).route
            ), "a route the teammate can use, on evidence that explains nothing"
        else:
            assert state(teammate) == FORK_PAUSE, (
                "the pause outlives the record, and the alternatives survive"
            )
    assert _alternatives_retained(teammate)


# --- The regression the record-first fix was for -----------------------------


@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_a_record_arriving_into_a_settled_lineage_still_pauses_nothing(view_class):
    """The review's counterexample from `a7ce749`, kept under both semantics."""
    teammate = actor("T", "teammate", view_class)
    teammate.view.receive(
        selection("sel-a", ROUTE_A), selection("sel-b", ROUTE_B, "sel-a")
    )
    settled = state(teammate)
    assert settled == settled_on("sel-b", ROUTE_B)

    teammate.view.receive(resolution_node("rec-1", ["sel-a"]))
    assert state(teammate) == settled, "a routeless node is never a tip"


# --- The resolver's own view, and the multi-parent control -------------------


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_gating_does_not_disturb_the_actor_that_made_the_resolution(
    view_class, chosen_id, other_id
):
    """The resolver holds the reference, so the gate is satisfied throughout."""
    resolver = _resolver(view_class, chosen_id, other_id)
    assert state(resolver) == settled_on("sel-r", resolver.view.get(chosen_id).route)

    resolver.select("sel-s", ROUTE_S, "sel-r")
    assert state(resolver) == settled_on("sel-s", ROUTE_S)

    forks = historical_forks(resolver.view)
    assert forks == {("sel-a", "sel-b")}, (
        "the real disagreement is history, and the record is not a party to one"
    )


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
def test_multi_parent_has_no_retirement_to_time(chosen_id, other_id):
    """The control: one artifact, so no schedule can separate the two effects."""
    resolver = actor("A", "device", IndependentView)
    resolver.select("sel-p", ROUTE_P)
    resolver.select("sel-a", ROUTE_A, "sel-p")
    resolver.select("sel-b", ROUTE_B, "sel-p")
    route = resolver.view.get(chosen_id).route
    resolver.select("sel-r", route, chosen_id, other_id)

    teammate = _teammate_holding_the_fork(IndependentView, resolver)
    assert state(teammate) == FORK_PAUSE
    teammate.deliver(resolver.announce("sel-r", publish_parents=True))
    assert state(teammate) == settled_on("sel-r", route)
    assert _alternatives_retained(teammate)


# --- Two records and no referencing route selection --------------------------

RECORD_ORDERS = [("rec-1", "rec-2"), ("rec-2", "rec-1")]
RECORD_ORDER_IDS = ["outer_first", "inner_first"]


def _two_orphan_records(view_class, other_id):
    """A teammate holding the fork, plus two records nothing routed names.

    `rec-1` names the branch a resolution would retire; `rec-2` names `rec-1`
    and nothing else. No route selection references either, so under the stated
    gate neither may retire anything.
    """
    teammate = actor("T", "teammate", view_class)
    teammate.view.receive(
        selection("sel-p", ROUTE_P),
        selection("sel-a", ROUTE_A, "sel-p"),
        selection("sel-b", ROUTE_B, "sel-p"),
    )
    return teammate, {
        "rec-1": resolution_node("rec-1", [other_id]),
        "rec-2": resolution_node("rec-2", ["rec-1"]),
    }


def _independent_after(record_ids, chosen_id, other_id, route):
    """What independent retirement does with whichever records are held."""
    if "rec-1" in record_ids:
        return settled_on(chosen_id, route)
    return FORK_PAUSE


@pytest.mark.parametrize("first,second", RECORD_ORDERS, ids=RECORD_ORDER_IDS)
@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_records_alone_never_activate_each_other(
    view_class, chosen_id, other_id, first, second
):
    """The review's counterexample to `a71d129`, in both delivery orders.

    Under gating the fork is untouched throughout: `rec-2` naming `rec-1` is
    not a route selection referencing it, so no gate opens. Independent
    retirement is the control and does retire, as it does for a lone record.
    """
    route = ROUTE_A if chosen_id == "sel-a" else ROUTE_B
    teammate, records = _two_orphan_records(view_class, other_id)
    assert state(teammate) == FORK_PAUSE

    held = []
    for record_id in (first, second, first, second):
        teammate.view.receive(records[record_id])
        held.append(record_id)
        if view_class is IndependentView:
            expected = _independent_after(held, chosen_id, other_id, route)
        else:
            expected = FORK_PAUSE
        assert state(teammate) == expected, (
            f"after delivering {held}, {view_class.__name__}"
        )

    assert _alternatives_retained(teammate)
    assert {"rec-1", "rec-2"} <= set(teammate.view.held), "records are retained"


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
def test_gating_still_activates_through_the_referencing_selection(
    chosen_id, other_id
):
    """The gate closes on record-to-record links without closing on the real one."""
    route = ROUTE_A if chosen_id == "sel-a" else ROUTE_B
    teammate, records = _two_orphan_records(GatedView, other_id)
    teammate.view.receive(records["rec-1"], records["rec-2"])
    assert state(teammate) == FORK_PAUSE

    teammate.view.receive(selection("sel-r", route, chosen_id, "rec-1"))
    assert state(teammate) == settled_on("sel-r", route)
    assert _alternatives_retained(teammate)


# --- The inspection report's subjects -----------------------------------------


def _report(a):
    """`inspect()` without the actor's name, which no expectation depends on."""
    return {k: v for k, v in a.inspect().items() if k != "actor"}


def _expected_report(tip, claims, forks, causes):
    return {
        "tips": frozenset({tip}) if isinstance(tip, str) else frozenset(tip),
        "claims": claims,
        "historical_forks": frozenset(forks),
        "incomplete": frozenset(),
        "causes": frozenset(causes),
    }


@pytest.mark.parametrize("chosen_id,other_id", SIDES, ids=SIDE_IDS)
@pytest.mark.parametrize("view_class", SEMANTICS, ids=SEMANTICS_IDS)
def test_the_history_report_names_only_route_selections(
    view_class, chosen_id, other_id
):
    """A record supplies ancestry; it is never a party to an incompatible pair.

    Checked at the three points where the report would be read: while the
    disagreement is live, once it is resolved, and after ordinary rotation.
    """
    resolver = actor("A", "device", view_class)
    resolver.select("sel-p", ROUTE_P)
    resolver.select("sel-a", ROUTE_A, "sel-p")
    resolver.select("sel-b", ROUTE_B, "sel-p")
    assert _report(resolver) == _expected_report(
        ("sel-a", "sel-b"),
        {("sel-a", "sel-b"): INCOMPATIBLE},
        set(),
        {(INCOMPATIBLE, ("sel-a", "sel-b"))},
    ), "before resolution the disagreement is current, not history"

    route = resolver.view.get(chosen_id).route
    resolver.view.receive(resolution_node("rec-1", [other_id]))
    resolver.select("sel-r", route, chosen_id, "rec-1")
    assert _report(resolver) == _expected_report(
        "sel-r", {}, {("sel-a", "sel-b")}, set()
    ), "after resolution the A/B fork is history and nothing pairs with rec-1"
    assert state(resolver) == settled_on("sel-r", route)

    resolver.select("sel-s", ROUTE_S, "sel-r")
    assert _report(resolver) == _expected_report(
        "sel-s", {}, {("sel-a", "sel-b")}, set()
    ), "rotation neither revives the fork nor adds a record to it"
    assert state(resolver) == settled_on("sel-s", ROUTE_S)
    assert "rec-1" in resolver.view.held, "the record stays inspectable evidence"
