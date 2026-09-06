"""Move 4 model: what should the public attestation carry?

Move 3 left one discriminator: whether a teammate receives the predecessor
selection ID, or whether provisional routing takes the scalar counter alone and
every conflict claim goes through a sibling holding private history.

This model reuses the Move 3 objects rather than restating them, because the
question is about the same actors and the same schedule. It runs no runtime
code. Importing works because pytest puts a test file's directory on the path.

Three worlds are compared:

  clean      P -> X1 -> X2, no fork, every link delivered.
  forked     P -> X1 -> X2 alongside P -> Y1; the teammate holds P, Y1 and X2,
             and X1 is withheld, exactly the Move 3 step 3 evidence.
  delayed    P -> X1 -> X2, no fork, but X1 has not arrived yet, so the
             teammate holds the same gap as `forked` with nothing wrong.

`clean` and `forked` are the pair that matters: one is safe and the other hides
a competing branch. `delayed` exists to price a strict recipient rule, because
a gap is not by itself evidence of a conflict.

Four teammate policies are compared, since a representation is only worth what
a recipient can do with it:

  PROVISIONAL_ONLY    route to the greatest counter held and claim nothing
                      about supersession, leaving retrospective checking to a
                      sibling holding private history. This is the honest
                      counter-only policy, and the fair comparison for links.
  ASSUME_SUPERSEDED   everything below the greatest counter is superseded.
                      A counter-only payload permits this policy; it does not
                      force it, which is why PROVISIONAL_ONLY exists.
  VERIFY_CHAIN        a selection is superseded only when the predecessor chain
                      from every leading selection reaches it with every link
                      held. Anything else is classified rather than flagged.
  REQUIRE_CHAIN       as VERIFY_CHAIN, but refuses to route at all until the
                      chain is complete.

Because PROVISIONAL_ONLY and VERIFY_CHAIN route identically in every world here,
the links are not defended as routing correctness. What they buy is diagnosis:
which rival is unaccounted for, and whether the gap is missing evidence, a
located branch or a live disagreement. Diagnosis is not settlement: nothing a
teammate holds establishes that a human resolved anything. Whether teammates
need that diagnosis enough to justify revising #224 is an argument this model
does not settle.

Assumptions, which the model asserts rather than establishes:

  - Selection IDs are opaque and carry no route content, so a predecessor link
    names a selection without disclosing where it pointed.
  - A teammate learns route content only from attestations delivered to it.
  - Everything the Move 3 model assumes about counters and private history.

Model success says nothing about the runtime.
"""
import dataclasses

import pytest

from model_succession_branches import (
    COUNTER_AND_PREDECESSOR,
    COUNTER_ONLY,
    SCHEMES,
    Device,
    ROUTE_P,
    ROUTE_X1,
    ROUTE_X2,
    ROUTE_Y1,
    Selection,
    Teammate,
    _diverged,
)

PROVISIONAL_ONLY = "provisional-only"
ASSUME_SUPERSEDED = "assume-superseded"
VERIFY_CHAIN = "verify-chain"
REQUIRE_CHAIN = "require-chain"

WORLDS = ["clean", "forked", "delayed"]
ROOT = "sel-p"


def _world(name, scheme):
    """Build one world and return the teammate's evidence for it."""
    a = Device("A", scheme)
    b = Device("B", scheme)
    p = Selection("sel-p", counter=1, predecessor_id=None, route=ROUTE_P)
    a.adopt(p)
    b.adopt(p)
    x1 = a.select("sel-x1", ROUTE_X1, predecessor_id="sel-p")
    if name == "forked":
        b.select("sel-y1", ROUTE_Y1, predecessor_id="sel-p")
    a.select("sel-x2", ROUTE_X2, predecessor_id="sel-x1")

    teammate = Teammate(scheme)
    teammate.deliver(a.attest("sel-p"))
    if name == "clean":
        teammate.deliver(a.attest("sel-x1"))
    elif name == "forked":
        teammate.deliver(b.attest("sel-y1"))
    teammate.deliver(a.attest("sel-x2"))
    expected = [1, 3] if name == "delayed" else [1, 2, 3]
    assert sorted(att.counter for att in teammate.selections.values()) == expected
    return teammate, x1


def view_signature(teammate):
    """Everything the teammate can observe that is not an opaque identifier.

    Route content is excluded deliberately: the question is whether the payload
    lets a recipient tell a safe world from a conflicted one, and it may not
    answer that by recognizing a particular berth name.
    """
    signature = []
    for att in teammate.selections.values():
        held = (
            att.predecessor_id in teammate.selections
            if att.predecessor_id is not None
            else None
        )
        signature.append((att.counter, att.predecessor_id is not None, held))
    return sorted(signature)


def report(teammate, policy, root_id=ROOT):
    """What the teammate routes to, what it claims, and what it cannot account for.

    The rival categories separate three different situations that a single
    "unresolved" flag conflates. A rival is:

      missing_evidence     some link between the leading selection, the rival
                           and the root is not held, so the observer cannot say
                           how they relate.
      located_divergence   every link is held and the branch point is visible,
                           and the rival is strictly older than the leader. The
                           relationship is located: this observer can say where
                           the branches parted and which one lost routing
                           priority.
      open_disagreement    every link is held, and the rival is not older, so it
                           still competes.

    The categories say where a rival sits, never that anyone settled it. A
    located divergence is not evidence of resolution: the counter that outranks
    it is not evidence that its author was observed, and
    `test_a_located_branch_is_not_evidence_that_anyone_resolved_it` shows that a
    deliberate resolution and an oblivious advance produce the same category.
    So no key here uses the word "resolved", and `unaccounted_rivals` names only
    the rivals this observer cannot place or that still compete.

    Classification is relative to the leading selections — those holding the
    greatest counter — and not to a route. When several selections tie, there is
    no route, and the tie is exactly the evidence the report exists to carry, so
    it is classified rather than dropped.
    """
    route = teammate.route()
    selections = teammate.selections
    contradicted = teammate.contradictions()
    if not selections:
        return {
            "route": route,
            "claims_superseded": set(),
            "missing_evidence": set(),
            "located_divergence": set(),
            "open_disagreement": set(),
            "unaccounted_rivals": set(),
            "chain_complete": False,
            "contradicted": set(contradicted),
        }
    top = max(a.counter for a in selections.values())
    leaders = [a for a in selections.values() if a.counter == top]
    complete = all(
        teammate.chain_complete(leader.selection_id, root_id) for leader in leaders
    )

    # With a unique leader the rivals are the other selections. With a tie the
    # leaders are rivals of each other too, which is the only way this report
    # can reach `open_disagreement` at all.
    leader_ids = {leader.selection_id for leader in leaders}
    if len(leaders) == 1:
        rivals = [a for a in selections.values() if a.selection_id not in leader_ids]
    else:
        rivals = list(selections.values())

    if policy == PROVISIONAL_ONLY:
        # Routes by counter and asserts nothing further. A counter-only payload
        # cannot support any other claim, and does not oblige the recipient to
        # make one.
        superseded, missing, divergent, open_rivals = set(), set(), set(), set()
    elif policy == ASSUME_SUPERSEDED:
        superseded = {a.selection_id for a in rivals if a.counter < top}
        missing, divergent, open_rivals = set(), set(), set()
    else:
        superseded = {
            a.selection_id
            for a in rivals
            if all(
                teammate.observed_replacement(leader.selection_id, a.selection_id)
                for leader in leaders
            )
        }
        missing, divergent, open_rivals = set(), set(), set()
        for att in rivals:
            if att.selection_id in superseded:
                continue
            if not complete or not teammate.chain_complete(
                att.selection_id, root_id
            ):
                missing.add(att.selection_id)
            elif att.counter < top:
                divergent.add(att.selection_id)
            else:
                open_rivals.add(att.selection_id)

    if policy == REQUIRE_CHAIN and not complete:
        route = None
    return {
        "route": route,
        "claims_superseded": superseded,
        "missing_evidence": missing,
        "located_divergence": divergent,
        "open_disagreement": open_rivals,
        "unaccounted_rivals": missing | open_rivals,
        "chain_complete": complete,
        "contradicted": set(contradicted),
    }


# --- The discriminator -----------------------------------------------------


def test_counter_only_cannot_tell_a_fork_from_ordinary_succession():
    """The safe world and the conflicted world look identical without links."""
    clean, _ = _world("clean", COUNTER_ONLY)
    forked, _ = _world("forked", COUNTER_ONLY)
    assert view_signature(clean) == view_signature(forked)

    clean, _ = _world("clean", COUNTER_AND_PREDECESSOR)
    forked, _ = _world("forked", COUNTER_AND_PREDECESSOR)
    assert view_signature(clean) != view_signature(forked)
    assert clean.chain_complete("sel-x2", "sel-p")
    assert not forked.chain_complete("sel-x2", "sel-p")


@pytest.mark.parametrize("scheme", SCHEMES)
def test_assuming_supersession_hides_the_rival_it_cannot_check(scheme):
    """One counter-only policy is equally confident in both worlds.

    This prices ASSUME_SUPERSEDED; it does not price the payload, because a
    counter-only recipient is not obliged to adopt this policy.
    """
    for world in ("clean", "forked"):
        teammate, _ = _world(world, scheme)
        assumed = report(teammate, ASSUME_SUPERSEDED)
        assert assumed["route"] == ROUTE_X2
        assert not assumed["unaccounted_rivals"]
        other = "sel-x1" if world == "clean" else "sel-y1"
        assert assumed["claims_superseded"] == {"sel-p", other}


@pytest.mark.parametrize("world", WORLDS)
def test_honest_counter_only_routes_correctly_and_claims_nothing(world):
    """The fair counter-only policy: same route, no unsupported claim.

    It is wrong in no world. Its whole cost is that it can say nothing about
    rivals, so a fork is found only out of band by a sibling with private
    history.
    """
    teammate, _ = _world(world, COUNTER_ONLY)
    honest = report(teammate, PROVISIONAL_ONLY)
    assert honest["route"] == ROUTE_X2
    assert not honest["claims_superseded"], "no supersession is claimed"
    assert not honest["unaccounted_rivals"]


@pytest.mark.parametrize("world", WORLDS)
def test_links_buy_diagnosis_not_routing_correctness(world):
    """The narrowed Move 4 comparison, stated as an assertion.

    Against honest counter-only routing the link changes no route. It changes
    what the recipient can say: counter-only reports the same silence in the
    safe and the conflicted world, while the linked report names the rival it
    cannot account for and why.
    """
    counter_only, _ = _world(world, COUNTER_ONLY)
    linked, _ = _world(world, COUNTER_AND_PREDECESSOR)
    honest = report(counter_only, PROVISIONAL_ONLY)
    verified = report(linked, VERIFY_CHAIN)
    assert honest["route"] == verified["route"], "routing is unchanged by links"
    if world == "forked":
        assert verified["missing_evidence"] == {"sel-p", "sel-y1"}
        assert not honest["missing_evidence"], (
            "counter-only names no rival, so the fork waits on a sibling"
        )


def test_verifying_the_chain_separates_the_two_worlds():
    """With links the same policy is quiet when safe and loud when not."""
    clean, _ = _world("clean", COUNTER_AND_PREDECESSOR)
    quiet = report(clean, VERIFY_CHAIN)
    assert quiet["route"] == ROUTE_X2
    assert quiet["claims_superseded"] == {"sel-p", "sel-x1"}
    assert not quiet["unaccounted_rivals"]
    assert quiet["chain_complete"]

    forked, _ = _world("forked", COUNTER_AND_PREDECESSOR)
    verified = report(forked, VERIFY_CHAIN)
    assert verified["route"] == ROUTE_X2, "routing stays provisional, not blocked"
    assert verified["missing_evidence"] == {"sel-p", "sel-y1"}
    assert not verified["claims_superseded"], "a missing link supports no claim"
    assert not verified["chain_complete"]


def test_counter_only_cannot_run_the_verifying_policy():
    """Without links every other selection is unresolved, in every world."""
    for world in ("clean", "forked"):
        teammate, _ = _world(world, COUNTER_ONLY)
        assert report(teammate, VERIFY_CHAIN)["unaccounted_rivals"] == {
            "sel-p",
            "sel-x1" if world == "clean" else "sel-y1",
        }


@pytest.mark.parametrize("world", WORLDS)
def test_requiring_a_complete_chain_stalls_on_ordinary_delay(world):
    """Why the link must be advisory: a gap is not evidence of a conflict."""
    teammate, _ = _world(world, COUNTER_AND_PREDECESSOR)
    strict = report(teammate, REQUIRE_CHAIN)
    if world == "clean":
        assert strict["route"] == ROUTE_X2
    else:
        # `delayed` is a correct, uncontested succession whose middle link is
        # merely late. A recipient that demands the chain refuses to route
        # there, and delivery it does not control decides when that ends.
        assert strict["route"] is None
        assert report(teammate, VERIFY_CHAIN)["route"] == ROUTE_X2


@pytest.mark.parametrize("world", WORLDS)
def test_links_disclose_no_route_content(world):
    """The exposure a predecessor link adds is an opaque name, not a history."""
    teammate, x1 = _world(world, COUNTER_AND_PREDECESSOR)
    named = {
        att.predecessor_id
        for att in teammate.selections.values()
        if att.predecessor_id is not None
    }
    unheld = named - set(teammate.selections)
    assert unheld == (set() if world == "clean" else {"sel-x1"})
    known_routes = {att.route for att in teammate.selections.values()}
    assert (x1.route in known_routes) == (world == "clean")


# --- The post-Move 4 review controls ---------------------------------------


def _resolved_world():
    """Move 3 step 5, with every record delivered after the human resolves.

    B adopts A's history, sees both branches, and deliberately reselects Y's
    location as Z at counter 4, naming X2 as its predecessor.
    """
    a, b, _x1, _y1, _x2 = _diverged(COUNTER_AND_PREDECESSOR)
    b.adopt_history(a)
    b.select("sel-z", ROUTE_Y1, predecessor_id="sel-x2")
    teammate = Teammate(COUNTER_AND_PREDECESSOR)
    for selection_id in ("sel-p", "sel-x1", "sel-x2", "sel-y1", "sel-z"):
        teammate.deliver(b.attest(selection_id))
    return b, teammate


def test_a_fully_visible_branch_is_located_rather_than_unaccounted_for():
    """Every record delivered, and Y1 is still off the routed chain.

    Z names X2 as its single predecessor, so no chain from Z reaches Y1 even
    though the human chose Y's location after observing both branches. The
    branch point is held, both branches are held, and Y1 is strictly older than
    the leader, so the observer can say where the branches parted. That is all
    it can say; the next control shows why it is not a claim about resolution.
    """
    _b, teammate = _resolved_world()
    located = report(teammate, VERIFY_CHAIN)

    assert located["route"] == ROUTE_Y1
    assert located["chain_complete"]
    assert located["claims_superseded"] == {"sel-p", "sel-x1", "sel-x2"}
    assert located["located_divergence"] == {"sel-y1"}
    assert not located["unaccounted_rivals"], (
        "a fully visible outranked branch is placed, not missing and not competing"
    )
    assert teammate.forks() == {"sel-p": {"sel-x1", "sel-y1"}}


def test_the_resolution_itself_is_not_public_evidence():
    """Whose evidence supports which report.

    The same delivered records are produced by a device that selected Z having
    never adopted Y1. The teammate therefore cannot distinguish a deliberate
    resolution from an accidental one, and must not report the human's choice
    as established. Only B's private history holds that evidence.
    """
    b, teammate = _resolved_world()
    assert {"sel-x1", "sel-y1"} <= set(b.history), "B chose Z holding both branches"

    unaware = Device("C", COUNTER_AND_PREDECESSOR)
    for selection_id in ("sel-p", "sel-x1", "sel-x2"):
        unaware.adopt(b.history[selection_id])
    unaware.select("sel-z", ROUTE_Y1, predecessor_id="sel-x2")
    assert "sel-y1" not in unaware.history

    assert unaware.attest("sel-z").claim == b.attest("sel-z").claim
    assert report(teammate, VERIFY_CHAIN)["located_divergence"] == {"sel-y1"}


@pytest.mark.parametrize("order", ["first_then_changed", "changed_then_first"])
def test_contradictory_attestations_for_one_selection_are_not_collapsed(order):
    """Move 2's contradiction obligation, carried into this model.

    A second signing act reuses the selection ID with a different counter and a
    different route. A projection keyed by selection alone would keep whichever
    arrived last, so delivery order would choose the route. Both claims must be
    retained, the selection must be reported as contradicted, and the
    contradiction must not be routed through.
    """
    b, teammate = _resolved_world()
    first = b.attest("sel-z")
    changed = dataclasses.replace(
        first, attestation_id="different-signing-act", counter=99, route=ROUTE_X1
    )
    assert changed.claim != first.claim

    recipient = Teammate(COUNTER_AND_PREDECESSOR)
    for att in teammate.attestations.values():
        recipient.deliver(att)
    pair = (first, changed) if order == "first_then_changed" else (changed, first)
    for att in pair:
        recipient.deliver(att)

    assert len(recipient.claims["sel-z"]) == 3, "both claims are retained"
    contradicted = report(recipient, VERIFY_CHAIN)
    assert contradicted["contradicted"] == {"sel-z"}
    assert contradicted["route"] is None, (
        "delivery order must not decide between conflicting claims"
    )
    assert "sel-z" not in recipient.selections


def _unresolved_world():
    """The Move 3 divergence with every record delivered and nothing resolved.

    A never observes Y1; it simply advances to X2 on its own branch. The
    teammate ends up holding exactly the records the resolved world delivers,
    minus the deliberate reselection.
    """
    a, b, _x1, _y1, _x2 = _diverged(COUNTER_AND_PREDECESSOR)
    teammate = Teammate(COUNTER_AND_PREDECESSOR)
    for selection_id, device in (
        ("sel-p", a),
        ("sel-x1", a),
        ("sel-x2", a),
        ("sel-y1", b),
    ):
        teammate.deliver(device.attest(selection_id))
    return a, teammate


def test_a_located_branch_is_not_evidence_that_anyone_resolved_it():
    """Counterexample 5: a complete chain places a branch; it settles nothing.

    Here no human ever chose between the branches — A advanced to X2 without
    observing Y1 at all — yet the delivered evidence classifies Y1 exactly as
    the resolved world does. Delivering the missing intermediate link changed
    what the observer can locate, not what anyone decided, so the category must
    not be read as settlement.
    """
    a, teammate = _unresolved_world()
    unresolved = report(teammate, VERIFY_CHAIN)

    assert "sel-y1" not in a.history, "A never observed the branch it outranks"
    assert not teammate.observed_replacement("sel-x2", "sel-y1")
    assert unresolved["route"] == ROUTE_X2
    assert unresolved["chain_complete"]
    assert unresolved["located_divergence"] == {"sel-y1"}

    _b, resolved_teammate = _resolved_world()
    resolved = report(resolved_teammate, VERIFY_CHAIN)
    assert resolved["located_divergence"] == unresolved["located_divergence"], (
        "the deliberate and the oblivious world produce the same category"
    )
    assert not (set(resolved) | set(unresolved)) & {
        "resolved",
        "historical_divergence",
    }, "the report claims no resolution anywhere"


@pytest.mark.parametrize("order", ["x1_then_y1", "y1_then_x1"])
def test_a_tie_is_classified_rather_than_dropped(order):
    """Counterexample 4: no route is not the same as no evidence.

    Two selections at the same counter leave the teammate with no route, which
    is correct — a tie is a conflict, not a choice. Returning empty sets there
    threw away the one thing the observer had actually established, and made
    `open_disagreement` unreachable: with a unique leader every rival is
    strictly older, so only a tie can produce a live competitor.
    """
    a, b, _x1, _y1, _x2 = _diverged(COUNTER_AND_PREDECESSOR)
    teammate = Teammate(COUNTER_AND_PREDECESSOR)
    teammate.deliver(a.attest("sel-p"))
    attestations = {"x1": a.attest("sel-x1"), "y1": b.attest("sel-y1")}
    for name in ("x1", "y1") if order == "x1_then_y1" else ("y1", "x1"):
        teammate.deliver(attestations[name])

    tied = report(teammate, VERIFY_CHAIN)
    assert tied["route"] is None, "a tie is a conflict, not a choice"
    assert tied["open_disagreement"] == {"sel-x1", "sel-y1"}
    assert tied["unaccounted_rivals"] == {"sel-x1", "sel-y1"}
    assert tied["claims_superseded"] == {"sel-p"}, "both leaders build on P"
    assert tied["chain_complete"]
    assert tied["located_divergence"] == set()
    assert teammate.equal_counter_conflicts() == {2: {"sel-x1", "sel-y1"}}
