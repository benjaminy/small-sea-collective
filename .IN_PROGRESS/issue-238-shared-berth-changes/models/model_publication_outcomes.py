"""Move 5 model: what may a device do after an uncertain publication outcome?

A standalone model like the earlier three. It runs no runtime code and uses no
runtime schema. It reuses the Move 3 objects for selections, counters and
private history, because the actors are the same and the question is what
happens at one boundary those models did not cross: publication of a selection
into the participant's shared evidence, where siblings adopt it.

Five boundaries are kept distinct, per ../plan.md's reporting requirement:

  local selection    the device mints a selection ID, a counter and a route.
  provider effect    storage is materialized. Not modeled; assumed done.
  publication        the selection is written to the participant's shared
                     store, from which siblings adopt it.
  attestation        the selection is signed for teammates (Move 2: signing is
                     not selecting).
  delivery           teammates receive it (Moves 3 and 4).

A publication attempt has one of four observed outcomes:

  ACCEPTED    the device holds an acknowledgment.
  UNKNOWN     the request was sent and no acknowledgment came back. The write
              may or may not have landed, and the device cannot tell.
  REFUSED     a definite refusal. Nothing was written.
  SUPERSEDED  refused because the store already holds a greater selection,
              which the refusal names.

The schedule is the one Move 5 names in ../plan.md:

  1. A selects X from P and publishes it. The write lands; the acknowledgment
     is lost, so A observes UNKNOWN.
  2. B adopts X from the store and deliberately replaces it with Y.
  3. A retries.

Two retry policies are compared, since the whole question is what a retry is:

  RETRY_SAME_RECORD       republish the identical selection record: same ID,
                          same counter, same predecessor, same route.
  RETRY_AS_NEW_SELECTION  make a fresh selection for the same intent.

Two restore policies are compared for the second half:

  RESTORE_REPLACES  imported historical evidence replaces what the device holds.
  RESTORE_UNIONS    imported evidence is added, and the greatest counter the
                    device has ever observed is a monotone high-water mark.

Assumptions, which the model asserts rather than establishes:

  - The shared store is a set of published selection records merged by union.
    Nothing here depends on the order in which records arrive or merge.
  - A device's private history is durable and is not the shared store. Crossing
    that boundary is an explicit publication, never a side effect.
  - Everything the Move 3 model assumes about counters and private history.
  - Provider effects are already done when a publication is attempted, so a
    refusal never unmakes storage.

Model success says nothing about whether the runtime supplies those guarantees.
"""
import dataclasses

import pytest

from model_succession_branches import (
    COUNTER_AND_PREDECESSOR,
    Device,
    ROUTE_P,
    ROUTE_X1,
    ROUTE_X2,
    ROUTE_Y1,
    Selection,
)

ACCEPTED = "accepted"
UNKNOWN = "unknown"
REFUSED = "refused"
SUPERSEDED = "superseded"
OUTCOMES = [ACCEPTED, UNKNOWN, REFUSED, SUPERSEDED]

RETRY_SAME_RECORD = "retry-same-record"
RETRY_AS_NEW_SELECTION = "retry-as-new-selection"
RETRY_POLICIES = [RETRY_SAME_RECORD, RETRY_AS_NEW_SELECTION]

ACKED = "acknowledged"
LOST = "acknowledgment-lost"
REJECTED = "rejected"

RESTORE_REPLACES = "restore-replaces"
RESTORE_UNIONS = "restore-unions"
RESTORE_POLICIES = [RESTORE_REPLACES, RESTORE_UNIONS]


# --- The shared store ------------------------------------------------------


class Store:
    """The participant's published per-berth evidence.

    Publication is idempotent on the selection ID: republishing an identical
    record is not a second publication, and it is how a retry is expressed.
    """

    def __init__(self):
        self.published = {}

    def head(self):
        """The published selection with the greatest counter, or None.

        A tie between distinct selections is a visible conflict, not a choice,
        exactly as in the teammate's provisional routing.
        """
        if not self.published:
            return None
        top = max(s.counter for s in self.published.values())
        tied = [s for s in self.published.values() if s.counter == top]
        return tied[0] if len(tied) == 1 else None

    def route(self):
        head = self.head()
        return None if head is None else head.route

    def conflicts(self):
        """Distinct published selections claiming the same counter."""
        by_counter = {}
        for selection in self.published.values():
            by_counter.setdefault(selection.counter, set()).add(selection.selection_id)
        return {c: ids for c, ids in by_counter.items() if len(ids) > 1}

    def merged_with(self, other):
        """Merging two stores is union; nothing depends on merge order."""
        merged = Store()
        merged.published = {**self.published, **other.published}
        return merged


@dataclasses.dataclass
class Attempt:
    """One publication attempt, as the device records it.

    `landed` is ground truth about the store. It is deliberately never read by
    any device method: the point of UNKNOWN is that the device cannot see it.
    """

    selection: Selection
    outcome: str
    landed: bool
    superseding: Selection | None = None


# --- Devices ---------------------------------------------------------------


class Participant(Device):
    """A Move 3 device that also publishes, retries and restores."""

    def __init__(self, name, restore_policy=RESTORE_UNIONS):
        super().__init__(name, COUNTER_AND_PREDECESSOR)
        self.restore_policy = restore_policy
        self.attempts = []
        self.publications = 0
        self.high_water = 0
        self.refused = set()

    def observe(self, selection):
        """Adopt a selection and remember the counter, whatever later happens."""
        self.adopt(selection)
        self.high_water = max(self.high_water, selection.counter)

    @property
    def greatest_counter(self):
        """The greatest counter observed, not merely the greatest still held.

        Under RESTORE_REPLACES the high-water mark is discarded with the rest
        of the device's state, which is the failure the restore control shows.
        """
        held = super().greatest_counter
        if self.restore_policy == RESTORE_REPLACES:
            return held
        return max(held, self.high_water)

    def select(self, selection_id, route, predecessor_id):
        selection = super().select(selection_id, route, predecessor_id)
        self.high_water = max(self.high_water, selection.counter)
        return selection

    def publish(self, store, selection, acknowledgment=ACKED):
        """Attempt to publish, and record what the device observes.

        `acknowledgment` describes the reply, not the write. A lost reply
        leaves the store changed and the device uninformed, which is the whole
        subject of this move.
        """
        self.publications += 1
        head = store.head()
        if head is not None and head.counter > selection.counter:
            attempt = Attempt(selection, SUPERSEDED, landed=False, superseding=head)
            self.refused.add(selection.selection_id)
            # A refusal that names a greater selection is evidence, and the
            # device must adopt it rather than merely quote it: attesting to a
            # selection means holding it, and the next deliberate selection has
            # to outrank what the device has now been told about.
            self.observe(head)
        elif acknowledgment == REJECTED:
            attempt = Attempt(selection, REFUSED, landed=False)
            self.refused.add(selection.selection_id)
        else:
            store.published[selection.selection_id] = selection
            outcome = ACCEPTED if acknowledgment == ACKED else UNKNOWN
            attempt = Attempt(selection, outcome, landed=True)
        self.attempts.append(attempt)
        return attempt

    def adopt_from(self, store):
        for selection in store.published.values():
            self.observe(selection)

    def retry(self, store, policy):
        """Retry the last attempt under one of the two policies."""
        last = self.attempts[-1]
        if policy == RETRY_SAME_RECORD:
            return self.publish(store, last.selection)
        fresh = self.select(
            f"{last.selection.selection_id}-retry",
            last.selection.route,
            predecessor_id=last.selection.predecessor_id,
        )
        return self.publish(store, fresh)

    def restore(self, snapshot):
        """Import historical evidence held outside the current state."""
        if self.restore_policy == RESTORE_REPLACES:
            self.history = dict(snapshot)
            self.high_water = 0
        else:
            for selection in snapshot.values():
                self.observe(selection)


# --- What a device may claim -----------------------------------------------


ATTEMPT_EFFECT = {
    ACCEPTED: "wrote the record",
    UNKNOWN: "may have written the record",
    REFUSED: "wrote nothing",
    SUPERSEDED: "wrote nothing",
}


def publication_claim(device, selection_id):
    """What the device may say about the record ever having been published.

    This is a different question from what the last attempt did, and the two
    must not share an answer. A refusal establishes that *this attempt* wrote
    nothing; it says nothing about whether an earlier attempt on the same
    record landed. So the claim is taken over every attempt on the record and
    never weakens: an acknowledged attempt makes it "published" permanently,
    and an unacknowledged one leaves it "unknown" no matter how many later
    attempts are refused.
    """
    outcomes = {
        a.outcome
        for a in device.attempts
        if a.selection.selection_id == selection_id
    }
    if ACCEPTED in outcomes:
        return "published"
    if UNKNOWN in outcomes:
        return "unknown"
    return "not published"


def standing(device):
    """What the device's own evidence supports after its last attempt.

    The discriminator is not how much the device knows. It is whether it has
    observed a conflict: REFUSED and SUPERSEDED are observed conflicts, and
    UNKNOWN is merely incomplete evidence, which the ledger already says a
    device may act on provisionally.

    Three things are reported separately because they answer three questions:
    `attempt_effect` is what the last attempt did, `publication_claim` is
    whether the record was ever published, and `routes_to` is current routing
    priority.
    """
    attempt = device.attempts[-1]
    selection = attempt.selection
    # A selection this device has itself seen refused is not a fallback
    # candidate. Eligibility has to survive across attempts: private history
    # records what was chosen, not what the store accepted.
    others = [
        s
        for s in device.history.values()
        if s.selection_id != selection.selection_id
        and s.selection_id not in device.refused
    ]
    best_other = max(others, key=lambda s: s.counter, default=None)

    if attempt.outcome == SUPERSEDED:
        candidate = attempt.superseding
    elif attempt.outcome == REFUSED:
        candidate = best_other
    elif best_other is not None and best_other.counter > selection.counter:
        # The attempt is not refused, but the device has since adopted a
        # greater selection. Publication outcome bounds what it may claim about
        # publication; it does not freeze what it routes to.
        candidate = best_other
    else:
        candidate = selection
    return {
        "routes_to": None if candidate is None else candidate.route,
        "may_attest": None if candidate is None else candidate.selection_id,
        "attempt_effect": ATTEMPT_EFFECT[attempt.outcome],
        "publication_claim": publication_claim(device, selection.selection_id),
        "must_surface": attempt.outcome in (REFUSED, SUPERSEDED),
    }


# --- The schedule ----------------------------------------------------------


def _published_p():
    """A store holding P, and the record itself."""
    store = Store()
    p = Selection("sel-p", counter=1, predecessor_id=None, route=ROUTE_P)
    store.published["sel-p"] = p
    return store, p


def _lost_acknowledgment(restore_policy=RESTORE_UNIONS):
    """Step 1: A publishes X, the write lands, the acknowledgment is lost."""
    store, p = _published_p()
    a = Participant("A", restore_policy)
    a.observe(p)
    x = a.select("sel-x", ROUTE_X1, predecessor_id="sel-p")
    attempt = a.publish(store, x, acknowledgment=LOST)
    assert attempt.outcome == UNKNOWN
    return store, a, x


def _sibling_replaces(store):
    """Step 2: B adopts the published X and deliberately replaces it."""
    b = Participant("B")
    b.adopt_from(store)
    y = b.select("sel-y", ROUTE_Y1, predecessor_id="sel-x")
    assert b.publish(store, y, acknowledgment=ACKED).outcome == ACCEPTED
    assert y.counter == 3
    return b, y


# --- Uncertain publication -------------------------------------------------


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_each_outcome_bounds_a_different_claim(outcome):
    """Unknown, refused and superseded are three different states, not degrees.

    An unknown outcome is the only one that permits proceeding, because it is
    the only one in which no conflict was observed.
    """
    store, p = _published_p()
    a = Participant("A")
    a.observe(p)
    if outcome == SUPERSEDED:
        other = Selection("sel-newer", 9, "sel-p", ROUTE_X2)
        store.published["sel-newer"] = other
    x = a.select("sel-x", ROUTE_X1, predecessor_id="sel-p")
    a.publish(
        store,
        x,
        {ACCEPTED: ACKED, UNKNOWN: LOST, REFUSED: REJECTED, SUPERSEDED: ACKED}[outcome],
    )
    view = standing(a)

    assert view["must_surface"] == (outcome in (REFUSED, SUPERSEDED))
    if outcome in (ACCEPTED, UNKNOWN):
        assert view["routes_to"] == ROUTE_X1
        assert view["may_attest"] == "sel-x"
    elif outcome == REFUSED:
        assert view["routes_to"] == ROUTE_P, "it falls back to what it still holds"
        assert view["may_attest"] == "sel-p"
    else:
        assert view["routes_to"] == ROUTE_X2, "the refusal named the winner"
        assert view["may_attest"] == "sel-newer"
    assert view["publication_claim"] == {
        ACCEPTED: "published",
        UNKNOWN: "unknown",
        REFUSED: "not published",
        SUPERSEDED: "not published",
    }[outcome], "one attempt, so the record's claim is the attempt's effect"
    assert view["attempt_effect"] == ATTEMPT_EFFECT[outcome]


@pytest.mark.parametrize("landed", [True, False])
def test_the_unknown_outcome_needs_no_ground_truth(landed):
    """The device behaves identically whether or not the write landed.

    This is what makes UNKNOWN usable: the device never has to learn which
    world it is in, and a retry is correct in both.
    """
    store, p = _published_p()
    a = Participant("A")
    a.observe(p)
    x = a.select("sel-x", ROUTE_X1, predecessor_id="sel-p")
    a.publish(store, x, acknowledgment=LOST)
    if not landed:
        del store.published["sel-x"]

    assert standing(a) == {
        "routes_to": ROUTE_X1,
        "may_attest": "sel-x",
        "attempt_effect": "may have written the record",
        "publication_claim": "unknown",
        "must_surface": False,
    }
    a.retry(store, RETRY_SAME_RECORD)
    assert store.published["sel-x"] == x, "the retry converges both worlds"
    assert a.publications == 2, "a retry is another attempt, not another selection"
    assert a.selections_made == 1


@pytest.mark.parametrize("policy", RETRY_POLICIES)
def test_retry_must_not_outrank_a_sibling_that_already_replaced_the_selection(policy):
    """The Move 5 schedule: lost acknowledgment, sibling replacement, retry.

    Republishing the identical record leaves B's deliberate replacement on top.
    Making a fresh selection for the same intent resurrects the route B chose
    against, which is the delayed-signing failure in another costume.
    """
    store, a, x = _lost_acknowledgment()
    _b, y = _sibling_replaces(store)
    a.adopt_from(store)
    assert standing(a)["routes_to"] == ROUTE_Y1, "A reconciles by adopting Y"
    assert standing(a)["publication_claim"] == "unknown", "and still cannot say"

    # The queued retry fires anyway: a retry is a background operation, and it
    # must be harmless even when the device has already moved on.
    a.retry(store, policy)

    if policy == RETRY_SAME_RECORD:
        assert store.head() == y
        assert store.route() == ROUTE_Y1
        assert a.selections_made == 1
    else:
        assert store.head().selection_id == "sel-x-retry"
        assert store.route() == ROUTE_X1, "retry promoted the replaced route"
        assert store.head().counter == 4 > y.counter


def test_retry_before_learning_of_the_replacement_manufactures_a_conflict():
    """The same fault without adoption: a tie nobody chose.

    A retries knowing only its own history, so a fresh selection lands on B's
    counter. The conflict is visible rather than silent, which is better and
    still wrong: no human chose anything here.
    """
    store, a, _x = _lost_acknowledgment()
    _b, _y = _sibling_replaces(store)
    a.retry(store, RETRY_AS_NEW_SELECTION)

    assert store.conflicts() == {3: {"sel-y", "sel-x-retry"}}
    assert store.route() is None, "a tie is a conflict, not a choice"


def test_deliberate_reselection_is_mechanically_a_retry_and_must_not_be_one():
    """Why the rule belongs at the retry call site.

    After A adopts Y, a human on A choosing X's location again produces exactly
    the record that RETRY_AS_NEW_SELECTION produced: same route, same counter,
    outranking Y. The protocol cannot tell them apart afterwards, so retry must
    be prevented from making selections rather than detected once it has.
    """
    store, a, _x = _lost_acknowledgment()
    _b, y = _sibling_replaces(store)
    a.adopt_from(store)

    chosen = a.select("sel-x3", ROUTE_X1, predecessor_id=y.selection_id)
    assert a.publish(store, chosen, acknowledgment=ACKED).outcome == ACCEPTED
    assert store.head() == chosen
    assert chosen.counter == 4, "a human choice outranks what it was chosen over"

    retried = Participant("A'")
    retried.observe(Selection("sel-p", 1, None, ROUTE_P))
    retried.observe(Selection("sel-x", 2, "sel-p", ROUTE_X1))
    retried.observe(y)
    look_alike = retried.select("sel-x-retry", ROUTE_X1, predecessor_id="sel-p")
    assert (look_alike.counter, look_alike.route) == (chosen.counter, chosen.route)


# --- The post-Move 5 review controls ---------------------------------------


def test_a_later_refusal_does_not_retract_an_earlier_unknown():
    """Counterexample 1: what an attempt did is not what the record is.

    The lost acknowledgment landed, so X is published. The queued retry is
    then refused as superseded, which is a true statement about that attempt
    and no statement at all about the earlier one. Collapsing the two into one
    field made the device claim its published record was never published.
    """
    store, a, x = _lost_acknowledgment()
    _b, y = _sibling_replaces(store)
    attempt = a.retry(store, RETRY_SAME_RECORD)
    assert attempt.outcome == SUPERSEDED
    assert store.published["sel-x"] == x, "the first attempt's write is still there"

    view = standing(a)
    assert view["attempt_effect"] == "wrote nothing", "true of this attempt"
    assert view["publication_claim"] == "unknown", (
        "a refusal of one attempt is not evidence that no attempt landed"
    )
    assert view["routes_to"] == ROUTE_Y1, "and routing priority is a third question"
    assert view["must_surface"]


def test_fallback_must_not_promote_a_selection_already_refused():
    """Counterexample 2: refusals have to survive the attempt that observed them.

    A is refused twice in a row. Falling back to the greatest selection still
    in private history picks the first refusal, because private history records
    what the device chose rather than what the store accepted.
    """
    store, p = _published_p()
    a = Participant("A")
    a.observe(p)
    x = a.select("sel-x", ROUTE_X1, predecessor_id="sel-p")
    assert a.publish(store, x, acknowledgment=REJECTED).outcome == REFUSED
    y = a.select("sel-y", ROUTE_Y1, predecessor_id="sel-p")
    assert a.publish(store, y, acknowledgment=REJECTED).outcome == REFUSED

    assert a.refused == {"sel-x", "sel-y"}
    view = standing(a)
    assert view["may_attest"] == "sel-p", "the only candidate never refused"
    assert view["routes_to"] == ROUTE_P
    assert store.published.keys() == {"sel-p"}


def test_a_superseding_refusal_must_be_adopted_not_merely_quoted():
    """Counterexample 3: the named winner is evidence, and evidence is adopted.

    The refusal names a selection at counter 9 that A has never held. Quoting
    it in a report while leaving A's history and high-water mark at 2 lets A
    attest to a record it does not hold, and lets a deliberate next selection
    take counter 3 — below the selection A was just told about.
    """
    store, p = _published_p()
    a = Participant("A")
    a.observe(p)
    newer = Selection("sel-newer", 9, "sel-p", ROUTE_X2)
    store.published["sel-newer"] = newer
    x = a.select("sel-x", ROUTE_X1, predecessor_id="sel-p")
    assert a.publish(store, x).outcome == SUPERSEDED

    assert standing(a)["may_attest"] == "sel-newer"
    assert a.history["sel-newer"] == newer, "attesting to it means holding it"
    assert a.greatest_counter == 9

    chosen = a.select("sel-next", ROUTE_Y1, predecessor_id="sel-newer")
    assert chosen.counter == 10, "a deliberate choice outranks what refused it"
    assert a.publish(store, chosen).outcome == ACCEPTED
    assert store.head() == chosen


# --- Merge and restore -----------------------------------------------------


def test_merging_published_stores_needs_no_rule_beyond_counter_ordering():
    """Union in either order, and the ordering the ledger already has decides."""
    store, a, x = _lost_acknowledgment()
    _b, y = _sibling_replaces(store)

    left = Store()
    left.published = {"sel-p": store.published["sel-p"], "sel-x": x}
    right = Store()
    right.published = {"sel-y": y}

    assert left.merged_with(right).head() == right.merged_with(left).head() == y
    assert not left.merged_with(right).conflicts()


def test_restoring_an_old_snapshot_of_the_store_revives_nothing():
    """Obsolete priority stays obsolete: an old record carries an old counter."""
    store, a, x = _lost_acknowledgment()
    snapshot = Store()
    snapshot.published = dict(store.published)
    _b, y = _sibling_replaces(store)

    assert store.merged_with(snapshot).head() == y
    assert store.merged_with(snapshot).published.keys() == store.published.keys()


def test_restore_must_not_publish_private_history():
    """The dangerous boundary is private evidence becoming published evidence.

    A refused candidate is in no store, so no merge of stores can promote it.
    It is in A's private history, so a restore that treats that history as
    publishable puts a definitely refused selection on top.
    """
    store, a, _x = _lost_acknowledgment()
    _b, y = _sibling_replaces(store)
    a.adopt_from(store)
    refused = a.select("sel-r", ROUTE_X2, predecessor_id=y.selection_id)
    assert a.publish(store, refused, acknowledgment=REJECTED).outcome == REFUSED
    assert "sel-r" not in store.published
    assert standing(a)["routes_to"] == ROUTE_Y1, "A falls back to what it holds"

    naive = Store()
    naive.published = dict(a.history)
    assert naive.head() == refused, "publishing private history promotes a refusal"
    assert store.merged_with(naive).route() == ROUTE_X2

    faithful = Store()
    faithful.published = dict(store.published)
    assert faithful.head() == y


@pytest.mark.parametrize("restore_policy", RESTORE_POLICIES)
def test_restore_must_not_lower_the_greatest_counter_observed(restore_policy):
    """The one rule merge and restore need beyond ordering.

    A restores an old snapshot of its own state and a human then chooses a
    route. Replacing state discards the counters A had seen, so the human's
    fresh choice lands below the selection it was chosen over and is refused as
    superseded. Keeping the high-water mark monotone is what lets the choice
    outrank it. This is the one rule the ordering does not already supply,
    because it constrains the device rather than the records.
    """
    store, a, _x = _lost_acknowledgment(restore_policy)
    _b, y = _sibling_replaces(store)
    a.adopt_from(store)
    old_snapshot = {"sel-p": store.published["sel-p"]}

    a.restore(old_snapshot)
    chosen = a.select("sel-x4", ROUTE_X1, predecessor_id="sel-p")
    attempt = a.publish(store, chosen, acknowledgment=ACKED)

    if restore_policy == RESTORE_REPLACES:
        assert chosen.counter == 2 < y.counter
        assert attempt.outcome == SUPERSEDED
        assert store.head() == y, "the human's choice cannot be published at all"
        # The refusal is visible rather than silent, so nothing is corrupted.
        # The damage is that a restore has made a deliberate choice
        # unpublishable, and no retry can fix it: only reselecting above the
        # counter A used to know about can.
    else:
        assert chosen.counter == 4 > y.counter
        assert attempt.outcome == ACCEPTED
        assert store.head() == chosen
