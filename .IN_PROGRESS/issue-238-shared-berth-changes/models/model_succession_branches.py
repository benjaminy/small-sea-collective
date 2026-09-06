"""Move 3 model: what does private selection history add to a public counter?

A standalone model, like ../models/model_sibling_repair.py. It runs no runtime
code and uses no runtime schema; the succession representation under test is a
candidate, not an accepted design.

The schedule is the bounded one from Move 3 in ../plan.md:

  1. A and B adopt P, then disconnect.
  2. A selects X1 and B selects Y1, both from P. A advances to X2 without ever
     observing Y1.
  3. A teammate receives X2 and Y1, in both orders, initially without X1.
  4. X1 arrives, or a sibling adopts A's private history, and the competing
     branches become inspectable.
  5. After discovery a human on B deliberately reselects Y's location as Z.
     Delayed delivery of X2 and repeated attestation of X2 must not reverse Z.

Two public representations are compared:

  - `COUNTER_ONLY`: the attestation carries a scalar succession counter.
  - `COUNTER_AND_PREDECESSOR`: it also names the selection it succeeds.

Two observers are kept distinct. `Teammate` sees only delivered attestations.
`Device` additionally holds its own private selection history, and may adopt a
sibling's, which is what NoteToSelf would carry. Teammates are never given
private history: #224 rejected a peer-verifiable selection DAG, and this
experiment does not reverse that.

Assumptions, which the model asserts rather than establishes:

  - A selection's private record survives later selections, so a chain can be
    walked backwards after the fact.
  - A sibling can adopt another sibling's private history as a unit.
  - Attestations are authentic; nothing here models signatures or trust
    revocation.
  - Each device knows its own greatest observed counter, so a new selection can
    exceed everything that device has seen.

Model success says nothing about whether NoteToSelf supplies those guarantees.
"""
import dataclasses
import itertools

import pytest

# --- Objects ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Route:
    protocol: str
    endpoint: str
    location: str
    account_region: str


@dataclasses.dataclass(frozen=True)
class Selection:
    """A device's private durable record of one selection."""

    selection_id: str
    counter: int
    predecessor_id: str | None
    route: Route


@dataclasses.dataclass(frozen=True)
class Attestation:
    """What a teammate actually receives."""

    attestation_id: str
    signer: str
    selection_id: str
    counter: int
    predecessor_id: str | None
    route: Route

    @property
    def claim(self):
        """What the attestation asserts, independent of who signed it.

        Move 2's comparison, carried forward: identity is excluded so that a
        fresh signing identity cannot hide a disagreement about the selection.
        """
        return (self.selection_id, self.counter, self.predecessor_id, self.route)


COUNTER_ONLY = "counter"
COUNTER_AND_PREDECESSOR = "counter+predecessor"
SCHEMES = [COUNTER_ONLY, COUNTER_AND_PREDECESSOR]


# --- Devices ---------------------------------------------------------------

_MINTED = itertools.count()


class Device:
    """A participant device: private selection history plus signing."""

    def __init__(self, name, scheme):
        self.name = name
        self.scheme = scheme
        self.history = {}
        self.selections_made = 0

    def adopt(self, selection):
        self.history[selection.selection_id] = selection

    def adopt_history(self, other):
        """Adopt a sibling's private history as a unit, per the assumptions."""
        self.history.update(other.history)

    @property
    def greatest_counter(self):
        return max((s.counter for s in self.history.values()), default=0)

    def select(self, selection_id, route, predecessor_id):
        """Choose a route, succeeding everything this device has observed."""
        self.selections_made += 1
        selection = Selection(
            selection_id=selection_id,
            counter=self.greatest_counter + 1,
            predecessor_id=predecessor_id,
            route=route,
        )
        self.adopt(selection)
        return selection

    def attest(self, selection_id):
        """Sign an attestation. Signing is not a selection (Move 2)."""
        selection = self.history[selection_id]
        return Attestation(
            attestation_id=f"att-{self.name}-{next(_MINTED)}",
            signer=self.name,
            selection_id=selection.selection_id,
            counter=selection.counter,
            predecessor_id=(
                selection.predecessor_id
                if self.scheme == COUNTER_AND_PREDECESSOR
                else None
            ),
            route=selection.route,
        )


# --- The teammate's view ---------------------------------------------------


class Teammate:
    """Holds delivered attestations. Sees no private history, ever."""

    def __init__(self, scheme):
        self.scheme = scheme
        self.attestations = {}

    def deliver(self, attestation):
        self.attestations[attestation.attestation_id] = attestation

    @property
    def claims(self):
        """Retained attestations grouped by the selection they name."""
        grouped = {}
        for att in self.attestations.values():
            grouped.setdefault(att.selection_id, []).append(att)
        return grouped

    def contradictions(self):
        """Selections whose retained attestations do not agree on their claim.

        Move 2's obligation applied to this model: projecting attestations into
        selections must not silently pick one of two conflicting claims, and
        distinct signing identities must not conceal the conflict.
        """
        return {
            selection_id: atts
            for selection_id, atts in self.claims.items()
            if len({a.claim for a in atts}) > 1
        }

    @property
    def selections(self):
        """One entry per distinct selection whose retained claims agree.

        A contradicted selection is deliberately absent rather than resolved by
        delivery order; `contradictions` is where it is reported.
        """
        contradicted = self.contradictions()
        return {
            selection_id: atts[0]
            for selection_id, atts in self.claims.items()
            if selection_id not in contradicted
        }

    def route(self):
        """Provisional routing: greatest counter wins.

        A tie between distinct selections is a visible conflict, not a choice,
        and so is a contradicted selection claiming the greatest counter.
        """
        selections = self.selections
        contradicted = self.contradictions()
        top = max(
            (a.counter for atts in self.claims.values() for a in atts), default=None
        )
        if top is None:
            return None
        if any(
            a.counter == top for atts in contradicted.values() for a in atts
        ):
            return None
        tied = [a for a in selections.values() if a.counter == top]
        if len({a.selection_id for a in tied}) > 1:
            return None
        return tied[0].route

    def equal_counter_conflicts(self):
        """The detection a counter-only recipient can perform."""
        by_counter = {}
        for att in self.selections.values():
            by_counter.setdefault(att.counter, set()).add(att.selection_id)
        return {c: ids for c, ids in by_counter.items() if len(ids) > 1}

    def observed_replacement(self, successor_id, other_id):
        """Does the retained evidence show `successor_id` replaced `other_id`?

        True only when the predecessor chain from the successor reaches the
        other selection with every link present. A counter-only recipient can
        never answer this, so it always reports False.
        """
        selections = self.selections
        current = selections.get(successor_id)
        while current is not None and current.predecessor_id is not None:
            if current.predecessor_id == other_id:
                return True
            current = selections.get(current.predecessor_id)
        return False

    def chain_complete(self, selection_id, root_id):
        """Whether every link from `selection_id` back to `root_id` is held."""
        selections = self.selections
        current = selections.get(selection_id)
        while current is not None:
            if current.selection_id == root_id:
                return True
            if current.predecessor_id is None:
                return False
            current = selections.get(current.predecessor_id)
        return False

    def forks(self):
        """Distinct selections naming the same predecessor."""
        by_predecessor = {}
        for att in self.selections.values():
            if att.predecessor_id is not None:
                by_predecessor.setdefault(att.predecessor_id, set()).add(
                    att.selection_id
                )
        return {p: ids for p, ids in by_predecessor.items() if len(ids) > 1}


def private_forks(device):
    """Forks visible in a device's private history."""
    by_predecessor = {}
    for selection in device.history.values():
        if selection.predecessor_id is not None:
            by_predecessor.setdefault(selection.predecessor_id, set()).add(
                selection.selection_id
            )
    return {p: ids for p, ids in by_predecessor.items() if len(ids) > 1}


# --- The schedule ----------------------------------------------------------

ROUTE_P = Route("s3", "https://minio.example/", "berth-p", "us-west")
ROUTE_X1 = Route("s3", "https://minio.example/", "berth-x1", "us-west")
ROUTE_X2 = Route("s3", "https://minio.example/", "berth-x2", "us-west")
ROUTE_Y1 = Route("s3", "https://minio.example/", "berth-y1", "eu-central")


def _diverged(scheme):
    """Steps 1 and 2: A and B adopt P, disconnect, and diverge."""
    a = Device("A", scheme)
    b = Device("B", scheme)
    p = Selection("sel-p", counter=1, predecessor_id=None, route=ROUTE_P)
    a.adopt(p)
    b.adopt(p)
    x1 = a.select("sel-x1", ROUTE_X1, predecessor_id="sel-p")
    y1 = b.select("sel-y1", ROUTE_Y1, predecessor_id="sel-p")
    x2 = a.select("sel-x2", ROUTE_X2, predecessor_id="sel-x1")
    assert (x1.counter, y1.counter, x2.counter) == (2, 2, 3)
    return a, b, x1, y1, x2


def _teammate_holding_p(scheme, a):
    """A teammate that already learned P before the participant diverged."""
    teammate = Teammate(scheme)
    teammate.deliver(a.attest("sel-p"))
    return teammate


def _deliver(teammate, attestations, order):
    for att in order:
        teammate.deliver(attestations[att])


ORDERS = list(itertools.permutations(["x2", "y1"]))


@pytest.mark.parametrize("scheme", SCHEMES)
def test_divergence_needs_no_reallocation_or_extra_selection(scheme):
    a, b, *_ = _diverged(scheme)
    assert (a.selections_made, b.selections_made) == (2, 1)


@pytest.mark.parametrize("order", ORDERS, ids=lambda o: "_".join(o))
@pytest.mark.parametrize("scheme", SCHEMES)
def test_higher_counter_routes_but_shows_no_replacement(scheme, order):
    """Step 3: X2 outranks Y1, and neither scheme calls that agreement."""
    a, b, x1, y1, x2 = _diverged(scheme)
    teammate = _teammate_holding_p(scheme, a)
    _deliver(teammate, {"x2": a.attest("sel-x2"), "y1": b.attest("sel-y1")}, order)

    assert teammate.route() == ROUTE_X2, "provisional routing follows the counter"
    assert not teammate.observed_replacement("sel-x2", "sel-y1"), (
        "a higher counter is not evidence that its author saw Y1"
    )
    assert not teammate.forks(), "the branch point is invisible without X1"
    assert not teammate.equal_counter_conflicts(), (
        "unequal counters are exactly what this schedule produces"
    )
    if scheme == COUNTER_AND_PREDECESSOR:
        # The one thing the predecessor field adds here: the teammate can see
        # that it is missing a link, so X2's claim is unverified rather than
        # simply higher.
        assert not teammate.chain_complete("sel-x2", "sel-p")
        assert teammate.chain_complete("sel-y1", "sel-p")


@pytest.mark.parametrize("order", list(itertools.permutations(["x2", "y1", "x1"])),
                         ids=lambda o: "_".join(o))
@pytest.mark.parametrize("scheme", SCHEMES)
def test_late_x1_exposes_the_fork_only_with_predecessors(scheme, order):
    """Step 4, teammate half: what delivering the missing link is worth."""
    a, b, x1, y1, x2 = _diverged(scheme)
    teammate = _teammate_holding_p(scheme, a)
    _deliver(
        teammate,
        {"x2": a.attest("sel-x2"), "y1": b.attest("sel-y1"), "x1": a.attest("sel-x1")},
        order,
    )

    assert teammate.route() == ROUTE_X2
    assert teammate.equal_counter_conflicts() == {2: {"sel-x1", "sel-y1"}}
    if scheme == COUNTER_AND_PREDECESSOR:
        assert teammate.forks() == {"sel-p": {"sel-x1", "sel-y1"}}
        assert teammate.chain_complete("sel-x2", "sel-p")
    else:
        assert not teammate.forks(), "counter-only cannot locate the branch point"
    # Under either scheme the counters alone still say nothing about who
    # observed whom.
    assert not teammate.observed_replacement("sel-x2", "sel-y1")


@pytest.mark.parametrize("scheme", SCHEMES)
def test_sibling_detects_the_fork_from_private_history(scheme):
    """Step 4, sibling half: adoption of A's history is what enables detection."""
    a, b, x1, y1, x2 = _diverged(scheme)
    assert not private_forks(b), "B alone holds only its own branch"

    b.adopt_history(a)
    assert private_forks(b) == {"sel-p": {"sel-x1", "sel-y1"}}
    # Detection needs the intermediate selection, not just the tip.
    partial = Device("B'", scheme)
    partial.adopt(Selection("sel-p", 1, None, ROUTE_P))
    partial.adopt(y1)
    partial.adopt(x2)
    assert not private_forks(partial)


@pytest.mark.parametrize(
    "order",
    list(itertools.permutations(["z", "x2_again", "x2_reattested"])),
    ids=lambda o: "_".join(o),
)
@pytest.mark.parametrize("scheme", SCHEMES)
def test_human_reselection_survives_delayed_delivery_and_reattestation(scheme, order):
    """Step 5: a fresh selection outranks the branch it was chosen over."""
    a, b, x1, y1, x2 = _diverged(scheme)
    b.adopt_history(a)
    before = b.selections_made
    z = b.select("sel-z", ROUTE_Y1, predecessor_id="sel-x2")
    assert z.counter == 4, "a new selection exceeds everything B has observed"
    assert b.selections_made == before + 1

    teammate = _teammate_holding_p(scheme, a)
    teammate.deliver(b.attest("sel-y1"))
    attestations = {
        "z": b.attest("sel-z"),
        "x2_again": a.attest("sel-x2"),
        "x2_reattested": b.attest("sel-x2"),
    }
    _deliver(teammate, attestations, order)

    assert teammate.route() == ROUTE_Y1, "the human's choice wins in every order"
    assert attestations["x2_again"].counter == attestations["x2_reattested"].counter, (
        "repeated attestation restates a selection; it does not advance one"
    )
    # The disagreement is not erased by resolution: B still holds both branches.
    assert private_forks(b) == {"sel-p": {"sel-x1", "sel-y1"}}
