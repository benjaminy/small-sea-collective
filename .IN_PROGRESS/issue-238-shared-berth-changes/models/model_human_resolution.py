"""Move 10 controls: what the reviewed human-resolution candidate gets wrong.

A standalone model, like ../models/model_succession_branches.py. It runs no
runtime code and uses no runtime schema. It preserves the three counterexamples
recorded in ../notes.md#counterexamples-to-preserve so that they cannot be lost
while a replacement is designed; it proposes no replacement.

The candidate under test is the one written down in
../contract-human-resolution.md: a selection names at most one predecessor, and
two selections *compete* when a device holds both, neither reaches the other
through predecessor links the device holds, and their route content differs.
Resolution mints a successor carrying the chosen alternative's route content.

Every test here passes by asserting that candidate's demonstrated failure, and
separately asserts what the complete evidence actually shows. A later candidate
is expected to reuse the same schedules — `resolution_naming_the_rejected`,
`resolution_naming_the_chosen`, `missing_intermediate` and
`missing_second_parent` — and to satisfy its own behavioral assertions on them,
rather than to change these controls.

Selections carry a set of parents even though the candidate never mints more
than one. The third control cannot be stated otherwise, and holding the general
shape here keeps the multi-parent candidate a modeling question rather than a
change to the vocabulary.

Assumptions, asserted rather than established:

  - A selection's record and its parent links survive later selections.
  - An actor's view is a set of selections it holds; delivery is modeled as
    adding one, and nothing is ever forgotten.
  - Route content equality is decidable and exact.

Nothing here says whether NoteToSelf or the Core chain supplies those
guarantees, and nothing here is evidence that a human reviewed anything.

The second half of the file is step 2: the four conclusions an actor may draw,
the tip/history separation, the candidate resume rule, and the separation of
inspection from integration, checked on the same schedules for a device, its
sibling and a receiving teammate. It is written against "ancestry the actor
holds" so that a candidate in step 3 can supply that from its own
representation. See ../obligations-actor-claims.md.
"""
import dataclasses

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
    """One durable selection record and the selections it claims to succeed.

    `parents` is `None` when the actor does not know what this selection
    succeeds, which is not the same as knowing it succeeds nothing. A teammate
    receiving an announcement that publishes no links is in the first case, and
    the difference decides what it may claim.
    """

    selection_id: str
    parents: frozenset | None
    route: Route


def selection(selection_id, route, *parents):
    return Selection(selection_id, frozenset(parents), route)


def unlinked(selection_id, route):
    """A selection whose ancestry the actor has not been told."""
    return Selection(selection_id, None, route)


class View:
    """What one actor holds. Teammate and sibling differ only in contents."""

    def __init__(self, *selections):
        self.held = {s.selection_id: s for s in selections}

    def receive(self, *selections):
        for s in selections:
            self.held[s.selection_id] = s
        return self

    def get(self, selection_id):
        return self.held.get(selection_id)

    def ancestors(self, selection_id):
        """Ancestors reachable through links this view actually holds.

        A named parent the view does not hold ends that path silently, which is
        the whole subject of the second and third controls.
        """
        seen = set()
        frontier = [selection_id]
        while frontier:
            current = self.held.get(frontier.pop())
            if current is None:
                continue
            for parent in current.parents or ():
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        return seen

    def ancestry_complete(self, selection_id):
        """Whether every parent named along the way is held."""
        frontier = [selection_id]
        while frontier:
            current = self.held.get(frontier.pop())
            if current is None or current.parents is None:
                return False
            frontier.extend(current.parents)
        return True

    def common_ancestors(self, left, right):
        return self.ancestors(left) & self.ancestors(right)


# --- The reviewed candidate ------------------------------------------------


def competing(view, left, right):
    """The reviewed contract's definition of an observed conflict."""
    if view.get(left) is None or view.get(right) is None:
        return False
    if left in view.ancestors(right) or right in view.ancestors(left):
        return False
    return view.get(left).route != view.get(right).route


def resolve(view, chosen_id, predecessor_id, resolution_id):
    """Mint the candidate's resolution: chosen content, one named predecessor."""
    return selection(resolution_id, view.get(chosen_id).route, predecessor_id)


# --- Route content ---------------------------------------------------------

ROUTE_P = Route("s3", "https://minio.example/", "berth-p", "us-west")
ROUTE_A = Route("s3", "https://minio.example/", "berth-a", "us-west")
ROUTE_B = Route("s3", "https://minio.example/", "berth-b", "eu-central")
ROUTE_S = Route("s3", "https://minio.example/", "berth-s", "eu-central")
ROUTE_X = Route("s3", "https://minio.example/", "berth-x", "us-west")
ROUTE_Y = Route("s3", "https://minio.example/", "berth-y", "us-west")
ROUTE_Z = Route("s3", "https://minio.example/", "berth-z", "eu-central")


# --- Schedules -------------------------------------------------------------


def _disagreement():
    """P, then two siblings selecting incompatible content from it."""
    p = selection("sel-p", ROUTE_P)
    a = selection("sel-a", ROUTE_A, "sel-p")
    b = selection("sel-b", ROUTE_B, "sel-p")
    return View(p, a, b), p, a, b


def resolution_naming_the_rejected():
    """A person keeps B's location; the resolution names A as predecessor."""
    view, p, a, b = _disagreement()
    r = resolve(view, "sel-b", "sel-a", "sel-r")
    view.receive(r)
    return view, r


def resolution_naming_the_chosen():
    """The same choice, with the resolution naming B as predecessor instead."""
    view, p, a, b = _disagreement()
    r = resolve(view, "sel-b", "sel-b", "sel-r")
    view.receive(r)
    return view, r


def missing_intermediate():
    """Clean X to Y to Z, delivered to a recipient that does not hold Y."""
    x = selection("sel-x", ROUTE_X)
    y = selection("sel-y", ROUTE_Y, "sel-x")
    z = selection("sel-z", ROUTE_Z, "sel-y")
    return View(x, z), x, y, z


def missing_second_parent():
    """A merge-shaped Z whose second parent path is the one not yet held."""
    p = selection("sel-p", ROUTE_P)
    x = selection("sel-x", ROUTE_X, "sel-p")
    y = selection("sel-y", ROUTE_Y, "sel-x")
    z = selection("sel-z", ROUTE_Z, "sel-p", "sel-y")
    return View(p, x, z), p, x, y, z


# --- Control 1: resolution plus ordinary rotation ---------------------------


def test_resolution_naming_the_rejected_reopens_after_a_routine_rotation():
    """Content equality hides the old alternative until the next rotation."""
    view, r = resolution_naming_the_rejected()
    assert view.ancestry_complete("sel-r"), "nothing here is missing evidence"
    assert not competing(view, "sel-r", "sel-b"), (
        "the candidate calls R and B compatible only because their content matches"
    )

    s = selection("sel-s", ROUTE_S, "sel-r")
    view.receive(s)
    assert view.ancestry_complete("sel-s")
    assert competing(view, "sel-s", "sel-b"), (
        "an ordinary rotation after a settled resolution competes with the "
        "alternative that resolution chose, with no fresh disagreement"
    )


def test_resolution_naming_the_chosen_leaves_the_rejected_competing():
    """The other single-predecessor option fails immediately instead."""
    view, r = resolution_naming_the_chosen()
    assert view.ancestry_complete("sel-r")
    assert competing(view, "sel-a", "sel-r"), (
        "naming B keeps the rejected alternative competing from the start"
    )


def test_no_single_predecessor_accounts_for_both_alternatives():
    """Neither choice of predecessor durably retires both branches."""
    late, _ = resolution_naming_the_rejected()
    late.receive(selection("sel-s", ROUTE_S, "sel-r"))
    early, _ = resolution_naming_the_chosen()
    assert competing(late, "sel-s", "sel-b")
    assert competing(early, "sel-a", "sel-r")


# --- Control 2: a missing link is not a human disagreement ------------------


def test_missing_intermediate_is_reported_as_a_conflict():
    view, x, y, z = missing_intermediate()
    assert competing(view, "sel-x", "sel-z"), (
        "the candidate cannot tell a gap from an incompatible choice"
    )
    assert not view.ancestry_complete("sel-z"), (
        "the evidence that would settle it is simply absent"
    )
    assert not view.common_ancestors("sel-x", "sel-z"), (
        "and there is no held branch point to point at"
    )


def test_the_missing_intermediate_alone_establishes_succession():
    view, x, y, z = missing_intermediate()
    view.receive(y)
    assert view.ancestry_complete("sel-z")
    assert "sel-x" in view.ancestors("sel-z")
    assert not competing(view, "sel-x", "sel-z"), (
        "nobody chose anything; one delivery removed the reported conflict"
    )


# --- Control 3: a held common ancestor does not establish a fork ------------


def test_a_held_common_ancestor_looks_like_a_fork():
    view, p, x, y, z = missing_second_parent()
    assert view.common_ancestors("sel-x", "sel-z") == {"sel-p"}, (
        "both tips reach a held branch point through links the view holds"
    )
    assert competing(view, "sel-x", "sel-z")
    assert not view.ancestry_complete("sel-z"), (
        "the only signal separating this from a real fork is the missing parent"
    )


def test_the_missing_second_parent_proves_supersession():
    view, p, x, y, z = missing_second_parent()
    view.receive(y)
    assert view.ancestry_complete("sel-z")
    assert "sel-x" in view.ancestors("sel-z")
    assert not competing(view, "sel-x", "sel-z")
    assert view.common_ancestors("sel-x", "sel-z") == {"sel-p"}, (
        "the held branch point is unchanged; it was never the deciding evidence"
    )


@pytest.mark.parametrize(
    "schedule", [missing_intermediate, missing_second_parent],
    ids=["missing_intermediate", "missing_second_parent"],
)
def test_incomplete_ancestry_accompanies_every_misreported_conflict(schedule):
    """The controls share one distinguishing fact the candidate ignores."""
    view = schedule()[0]
    assert competing(view, "sel-x", "sel-z")
    assert not view.ancestry_complete("sel-z")


# ===========================================================================
# Step 2: what each actor can actually conclude
# ===========================================================================
#
# Everything below states obligations, not a representation. The classifier is
# written against "ancestry the actor holds"; a candidate in step 3 supplies
# that from whatever links it publishes or retains, and must then satisfy these
# same assertions on the schedules above.
#
# The four conclusions are kept apart deliberately. Two of them are the ones
# the reviewed candidate ran together, and the controls above are what that
# cost.


SUPERSESSION = "supersession"
INCOMPATIBLE = "incompatible"
INCOMPLETE = "incomplete-ancestry"
SAME_CONTENT = "same-content"
CONTRADICTION = "contradictory-payload"


def classify(view, left, right):
    """What an actor holding both selections may claim about the pair.

    `INCOMPLETE` is a refusal to claim, not a weaker conflict: a parent the
    view does not hold could still turn the pair into `SUPERSESSION`, which is
    exactly what controls 2 and 3 demonstrate. A held common ancestor does not
    change that, and is deliberately not consulted here.
    """
    if left in view.ancestors(right) or right in view.ancestors(left):
        return SUPERSESSION
    if not (view.ancestry_complete(left) and view.ancestry_complete(right)):
        return INCOMPLETE
    if view.get(left).route == view.get(right).route:
        return SAME_CONTENT
    return INCOMPATIBLE


def tips(view):
    """Held selections that nothing else held supersedes.

    Retained history is evidence; only tips are candidates for the current
    route. Separating them is what keeps a resolved disagreement from staying
    active merely because its branches were preserved.
    """
    superseded = set()
    for selection_id in view.held:
        superseded |= view.ancestors(selection_id)
    # A node carrying no route is ancestry only. It can retire what it names,
    # but it is never itself a candidate, so arriving ahead of the selection
    # that references it leaves the current tips alone.
    return {
        s for s in view.held
        if s not in superseded and view.held[s].route is not None
    }


def pair_claims(view, among=None):
    """Every pairwise conclusion, over `among` (default: the tips)."""
    subjects = sorted(tips(view) if among is None else among)
    return {
        (left, right): classify(view, left, right)
        for i, left in enumerate(subjects)
        for right in subjects[i + 1 :]
    }


def historical_forks(view):
    """Pairs that were incompatible choices but are no longer both current.

    Only route-carrying selections are subjects. An incompatible choice is a
    choice of route, so a node with no route of its own is ancestry and
    inspectable evidence, never a party to a fork. It stays in `view.held` and
    keeps supplying ancestry to the pairs that are subjects.
    """
    routed = {s for s in view.held if view.held[s].route is not None}
    return {
        pair
        for pair, claim in pair_claims(view, among=routed).items()
        if claim == INCOMPATIBLE and not set(pair) <= tips(view)
    }


# --- Actors ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Announcement:
    """What a teammate receives: a signed claim, with no private history.

    `parents` is what the representation chooses to publish. Today's runtime
    publishes none, and #224 rejected a peer-verifiable selection DAG, so the
    empty set is the honest default rather than an omission.
    """

    signer: str
    selection_id: str
    route: Route
    parents: frozenset | None = None

    @property
    def claim(self):
        """The assertion, independent of who signed it (Move 2)."""
        return (self.selection_id, self.route, self.parents)


class Actor:
    """One participant device, its sibling, or a receiving teammate.

    The kinds differ only in what evidence they may hold, which is the point:
    a device holds its own selection records and may adopt a sibling's, and a
    teammate holds announcements and never private history.
    """

    def __init__(self, name, kind):
        self.name = name
        self.kind = kind
        self.view = View()
        self.announcements = set()
        self.published = []
        self.human_hold = False

    def select(self, selection_id, route, *parents):
        assert self.kind != "teammate", "a teammate mints no selections"
        s = selection(selection_id, route, *parents)
        self.view.receive(s)
        return s

    def adopt_history(self, other):
        """NoteToSelf delivery of a sibling's private records, as a unit."""
        assert self.kind != "teammate", "#224: teammates never receive private history"
        self.view.receive(*other.view.held.values())

    def deliver(self, announcement):
        # Every distinct announcement is retained. Keying by signer would let a
        # second payload from the same signer erase the first, and with it the
        # only evidence of the contradiction.
        self.announcements.add(announcement)
        if self.kind == "teammate" and announcement.selection_id not in self.contradicted():
            self.view.receive(
                unlinked(announcement.selection_id, announcement.route)
                if announcement.parents is None
                else selection(
                    announcement.selection_id,
                    announcement.route,
                    *announcement.parents,
                )
            )

    def contradicted(self):
        """Selection IDs whose retained announcements do not agree.

        A contradiction is about one identity, not a pair of choices, and a
        fresh signing identity must not conceal it.
        """
        by_selection = {}
        for a in self.announcements:
            by_selection.setdefault(a.selection_id, set()).add(a.claim)
        return {s for s, claims in by_selection.items() if len(claims) > 1}

    def announce(self, selection_id, publish_parents):
        """Sign a claim about a selection this actor holds.

        Signing is not selecting, and a merge-shaped claim is not evidence that
        a person reviewed anything; nothing here records human involvement.
        """
        s = self.view.get(selection_id)
        return Announcement(
            self.name,
            s.selection_id,
            s.route,
            s.parents if publish_parents else None,
        )

    # --- Pause and resumption ---------------------------------------------

    def pause_causes(self):
        """Why this actor is paused, one entry per distinct cause.

        A cause is a fact about held evidence, so it disappears exactly when
        the evidence that produced it changes.
        """
        causes = {(CONTRADICTION, s) for s in self.contradicted()}
        for pair, claim in pair_claims(self.view).items():
            if claim in (INCOMPATIBLE, INCOMPLETE):
                causes.add((claim, pair))
        return causes

    def may_resume(self):
        """Candidate rule: no cause remains, and no person is holding it.

        Missing history may never arrive, so this promises no progress; it only
        says that when the cause is gone, waiting further is not required.
        """
        return not self.pause_causes() and not self.human_hold

    # --- Inspection versus integration -------------------------------------

    def inspect(self):
        """Produce a report. Changes no live state and publishes nothing."""
        return {
            "actor": self.name,
            "tips": frozenset(tips(self.view)),
            "claims": dict(pair_claims(self.view)),
            "historical_forks": frozenset(historical_forks(self.view)),
            "incomplete": frozenset(
                s for s in self.view.held if not self.view.ancestry_complete(s)
            ),
            "causes": frozenset(self.pause_causes()),
        }

    def publish(self, announcement):
        assert self.may_resume(), "a paused actor publishes nothing"
        self.published.append(announcement)
        return announcement


def _fingerprint(actor):
    """Everything an inspection must leave alone."""
    return (
        {k: v for k, v in actor.view.held.items()},
        set(actor.announcements),
        list(actor.published),
    )


# --- What each actor may claim on the preserved schedules -------------------


def _device_pair(schedule):
    """A device holding the schedule's evidence, and its uninformed sibling."""
    view = schedule()[0]
    device = Actor("A", "device")
    device.view = view
    sibling = Actor("B", "device")
    return device, sibling


def test_a_device_holding_complete_evidence_may_claim_incompatibility():
    device, _ = _device_pair(resolution_naming_the_chosen)
    assert classify(device.view, "sel-a", "sel-r") == INCOMPATIBLE
    assert classify(device.view, "sel-b", "sel-r") == SUPERSESSION
    assert tips(device.view) == {"sel-a", "sel-r"}, (
        "the rejected alternative is still current, which is the control's point"
    )


@pytest.mark.parametrize(
    "schedule", [missing_intermediate, missing_second_parent],
    ids=["missing_intermediate", "missing_second_parent"],
)
def test_missing_ancestry_permits_no_incompatibility_claim(schedule):
    """The correction the controls demand, stated as a permissible claim."""
    device, _ = _device_pair(schedule)
    assert classify(device.view, "sel-x", "sel-z") == INCOMPLETE
    assert competing(device.view, "sel-x", "sel-z"), (
        "the reviewed candidate still calls this a conflict"
    )
    assert (INCOMPLETE, ("sel-x", "sel-z")) in device.pause_causes()
    assert not any(c[0] == INCOMPATIBLE for c in device.pause_causes())


def test_a_held_common_ancestor_does_not_license_the_claim():
    device, _ = _device_pair(missing_second_parent)
    assert device.view.common_ancestors("sel-x", "sel-z") == {"sel-p"}
    assert classify(device.view, "sel-x", "sel-z") == INCOMPLETE


def test_an_uninformed_sibling_is_not_in_disagreement():
    """A device that has not received the other's selection has no claim."""
    device, sibling = _device_pair(resolution_naming_the_chosen)
    sibling.select("sel-p", ROUTE_P)
    sibling.select("sel-b", ROUTE_B, "sel-p")
    assert not sibling.pause_causes()
    assert sibling.may_resume()
    sibling.adopt_history(device)
    assert (INCOMPATIBLE, ("sel-a", "sel-r")) in sibling.pause_causes()


def test_a_teammate_without_published_ancestry_can_conclude_nothing():
    """#224's boundary, priced: every pair collapses to one refusal to claim."""
    device, _ = _device_pair(missing_intermediate)
    teammate = Actor("T", "teammate")
    for selection_id in ("sel-x", "sel-z"):
        teammate.deliver(device.announce(selection_id, publish_parents=False))

    assert set(pair_claims(teammate.view).values()) == {INCOMPLETE}, (
        "with no published links the teammate cannot separate a rotation from "
        "a disagreement, so it may claim neither"
    )
    assert tips(teammate.view) == {"sel-x", "sel-z"}


def test_publishing_ancestry_is_what_lets_a_teammate_claim_supersession():
    device, _ = _device_pair(missing_intermediate)
    device.view.receive(missing_intermediate()[2])
    teammate = Actor("T", "teammate")
    for selection_id in ("sel-x", "sel-y", "sel-z"):
        teammate.deliver(device.announce(selection_id, publish_parents=True))

    assert classify(teammate.view, "sel-x", "sel-z") == SUPERSESSION
    assert teammate.may_resume()


def test_a_contradicted_identity_is_a_separate_cause():
    """Two signers, one selection ID, different route content."""
    device, _ = _device_pair(missing_intermediate)
    teammate = Actor("T", "teammate")
    teammate.deliver(device.announce("sel-x", publish_parents=True))
    teammate.deliver(Announcement("B", "sel-x", ROUTE_Y))

    assert teammate.contradicted() == {"sel-x"}
    assert (CONTRADICTION, "sel-x") in teammate.pause_causes()


def test_one_signer_contradicting_itself_is_not_forgotten():
    """A second payload from the same signer must not replace the first."""
    teammate = Actor("T", "teammate")
    teammate.deliver(Announcement("A", "sel-x", ROUTE_X))
    teammate.deliver(Announcement("A", "sel-x", ROUTE_Y))

    assert teammate.contradicted() == {"sel-x"}
    assert not teammate.may_resume()
    assert teammate.view.get("sel-x").route == ROUTE_X, (
        "the later payload did not quietly rewrite what the teammate holds"
    )


def test_redelivering_the_same_announcement_contradicts_nothing():
    teammate = Actor("T", "teammate")
    announcement = Announcement("A", "sel-x", ROUTE_X)
    teammate.deliver(announcement)
    teammate.deliver(announcement)

    assert teammate.contradicted() == set()
    assert teammate.may_resume()


# --- Obligations the classification itself must meet ------------------------


def test_matching_route_content_does_not_merge_selection_identities():
    view = View(
        selection("sel-p", ROUTE_P),
        selection("sel-a", ROUTE_B, "sel-p"),
        selection("sel-b", ROUTE_B, "sel-p"),
    )
    assert classify(view, "sel-a", "sel-b") == SAME_CONTENT
    assert tips(view) == {"sel-a", "sel-b"}, (
        "two devices choosing the same location made two selections, and the "
        "resolution vocabulary must still be able to name each of them"
    )


def test_retained_history_does_not_keep_a_resolved_disagreement_active():
    """Preserving evidence is not the same as leaving the dispute current."""
    view, _ = _disagreement()[0], None
    view.receive(selection("sel-r", ROUTE_B, "sel-a", "sel-b"))
    assert tips(view) == {"sel-r"}
    assert historical_forks(view) == {("sel-a", "sel-b")}
    assert not pair_claims(view), "nothing incompatible remains current"
    # The merge shape is what retires both branches here. It is exercised as a
    # possibility the vocabulary must be able to express, and is neither an
    # accepted representation nor evidence that a person reviewed anything.


def test_the_resume_rule_needs_the_cause_gone_and_no_human_hold():
    device, _ = _device_pair(missing_intermediate)
    assert not device.may_resume()
    device.view.receive(missing_intermediate()[2])
    assert not device.pause_causes()
    assert device.may_resume()
    device.human_hold = True
    assert not device.may_resume(), "a person may keep their own device paused"


def test_inspection_changes_nothing_and_publishes_nothing():
    device, _ = _device_pair(resolution_naming_the_chosen)
    before = _fingerprint(device)
    report = device.inspect()
    assert report["claims"][("sel-a", "sel-r")] == INCOMPATIBLE
    assert _fingerprint(device) == before
    assert not device.published


def test_a_paused_actor_publishes_nothing():
    device, _ = _device_pair(resolution_naming_the_chosen)
    with pytest.raises(AssertionError):
        device.publish(device.announce("sel-r", publish_parents=True))
    assert not device.published
