"""Move 2 model: does separating attestation identity from selection matter?

This is a standalone model, not a probe. It runs no runtime code and uses no
runtime schema: the contract in ../contract-sibling-repair.md is restated here
in its own terms so the two candidate identity schemes can be compared without
inheriting today's table boundaries.

The schedules are the two from Move 2 in ../plan.md, each in both delivery
orders:

  1. A finalizes durable evidence for one selection and is interrupted before
     signing. B adopts the evidence and attests. A resumes and attests. The
     repair must create neither a successor selection nor another allocation,
     and the duplicate attestations must be harmless.
  2. B attests to different route content, or to a different predecessor, under
     the same claimed selection ID. Once both attestations have arrived the
     contradiction must remain visible, in either order.

Each schedule runs under both candidates (independently minted attestation IDs,
and an ID reserved at selection time that permits multiple signers) and under
both recipient retention policies (keyed by attestation identity, or keyed by
identity and payload). The retention policy is varied because the contract
requires the reserved candidate to be given a recipient that can retain
conflicting signed payloads; varying it shows what that requirement costs.

Assumptions, which the model asserts rather than establishes:

  - The durable evidence record is adopted as a unit; a sibling never sees part
    of it. `Device.adopt` copies the whole record.
  - Adoption is distinguishable from reachability. Nothing here observes
    storage, and no recipient conclusion depends on reachability.
  - Predecessor evidence survives a selection change, so a recipient can order
    selections by their succession fields.

Model success says nothing about whether NoteToSelf supplies those guarantees.
"""
import dataclasses
import itertools

import pytest

# --- The contract's objects ------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Route:
    """Exact route content, frozen into the evidence rather than referenced."""

    protocol: str
    endpoint: str
    location: str
    account_region: str


@dataclasses.dataclass(frozen=True)
class Evidence:
    """The durable per-berth record a sibling adopts before it may attest."""

    selection_id: str
    predecessor_id: str | None
    allocation_id: str
    route: Route
    materialized: bool


@dataclasses.dataclass(frozen=True)
class Attestation:
    attestation_id: str
    signer: str
    selection_id: str
    predecessor_id: str | None
    route: Route

    @property
    def claim(self):
        """What the attestation asserts, independent of who signed it."""
        return (self.selection_id, self.predecessor_id, self.route)


# --- The two candidate identity schemes ------------------------------------


class SeparateIdentities:
    """Each signing act mints its own attestation identity."""

    name = "separate"
    reserves_identity = False

    def __init__(self):
        self._minted = itertools.count()

    def identity(self, evidence, signer):
        return f"att-{signer}-{next(self._minted)}"


class ReservedIdentity:
    """One identity is reserved at selection time and shared by all signers.

    The reservation is part of the durable evidence, so a sibling that has
    adopted the evidence knows the identity without talking to the interrupted
    device. No signer is fixed: the contract requires this candidate to be
    modelled with multiple signers permitted.
    """

    name = "reserved"
    reserves_identity = True

    def identity(self, evidence, signer):
        return f"att-{evidence.selection_id}"


# --- Devices ---------------------------------------------------------------

_UNSET = object()


class Device:
    def __init__(self, name, scheme):
        self.name = name
        self.scheme = scheme
        self.adopted = {}
        self.selections_made = 0
        self.allocations_made = 0

    def select(self, selection_id, predecessor_id, route, allocation_id=None):
        """Make a new selection, allocating storage only when asked to."""
        self.selections_made += 1
        if allocation_id is None:
            self.allocations_made += 1
            allocation_id = f"alloc-{self.name}-{self.allocations_made}"
        evidence = Evidence(
            selection_id=selection_id,
            predecessor_id=predecessor_id,
            allocation_id=allocation_id,
            route=route,
            materialized=True,
        )
        self.adopted[selection_id] = evidence
        return evidence

    def adopt(self, evidence):
        self.adopted[evidence.selection_id] = evidence

    def attest(self, selection_id, route=None, predecessor_id=_UNSET):
        """Sign an attestation. Signing is not a selection.

        `route` and `predecessor_id` override the adopted values only so the
        contradiction schedules can be expressed; a faithful device passes
        neither.
        """
        if selection_id not in self.adopted:
            raise PermissionError(
                f"{self.name} has not adopted evidence for {selection_id}"
            )
        evidence = self.adopted[selection_id]
        return Attestation(
            attestation_id=self.scheme.identity(evidence, self.name),
            signer=self.name,
            selection_id=selection_id,
            predecessor_id=(
                evidence.predecessor_id if predecessor_id is _UNSET else predecessor_id
            ),
            route=evidence.route if route is None else route,
        )


# --- Recipients ------------------------------------------------------------

IDENTITY_KEYED = "identity"
PAYLOAD_KEYED = "identity+payload"


class Recipient:
    """Holds delivered attestations and reports what it may conclude.

    `retention` says what the recipient keys stored attestations by. Keying by
    identity alone is the behaviour a store gets for free when the identity is
    its primary key; keying by identity and payload is the behaviour the
    contract requires of the reserved candidate.
    """

    def __init__(self, retention):
        self.retention = retention
        self._stored = {}

    def deliver(self, attestation):
        if self.retention == IDENTITY_KEYED:
            key = attestation.attestation_id
        else:
            key = (attestation.attestation_id, attestation.signer, attestation.claim)
        self._stored[key] = attestation

    @property
    def retained(self):
        return list(self._stored.values())

    def view(self):
        """Conclusions, per the contract's four recipient rules."""
        by_selection = {}
        for att in self.retained:
            by_selection.setdefault(att.selection_id, []).append(att)

        contradictions = {
            selection_id: atts
            for selection_id, atts in by_selection.items()
            if len({a.claim for a in atts}) > 1
        }
        # Ordering across selections comes from the payload's succession
        # fields, never from attestation or signer identity.
        latest = _latest_selection(by_selection)
        return {
            "selections": set(by_selection),
            "contradictions": contradictions,
            "route": None if latest is None or latest in contradictions
            else by_selection[latest][0].route,
        }


def _latest_selection(by_selection):
    """The selection no other retained selection names as its predecessor."""
    if not by_selection:
        return None
    claimed_predecessors = {
        att.predecessor_id for atts in by_selection.values() for att in atts
    }
    tips = [s for s in by_selection if s not in claimed_predecessors]
    return tips[0] if len(tips) == 1 else None


# --- Schedules -------------------------------------------------------------

ROUTE_X = Route("s3", "https://minio.example/", "berth-core-x", "us-west")
SCHEMES = [SeparateIdentities, ReservedIdentity]
RETENTIONS = [IDENTITY_KEYED, PAYLOAD_KEYED]
ORDERS = ["a_then_b", "b_then_a"]


def _interrupted_signing(scheme_cls):
    """A selects and is interrupted before signing; B adopts and attests."""
    scheme = scheme_cls()
    a = Device("A", scheme)
    b = Device("B", scheme)
    evidence = a.select("sel-2", predecessor_id="sel-1", route=ROUTE_X)
    b.adopt(evidence)
    return a, b, evidence


@pytest.mark.parametrize("scheme_cls", SCHEMES, ids=lambda c: c.name)
def test_unadopted_sibling_may_not_attest(scheme_cls):
    a, b, evidence = _interrupted_signing(scheme_cls)
    c = Device("C", a.scheme)
    with pytest.raises(PermissionError):
        c.attest(evidence.selection_id)


@pytest.mark.parametrize("retention", RETENTIONS)
@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("scheme_cls", SCHEMES, ids=lambda c: c.name)
def test_repair_duplicates_are_harmless(scheme_cls, order, retention):
    """Case 1: both candidates repair without a successor or a reallocation."""
    a, b, evidence = _interrupted_signing(scheme_cls)
    by_b = b.attest(evidence.selection_id)
    by_a = a.attest(evidence.selection_id)

    recipient = Recipient(retention)
    for att in (by_a, by_b) if order == "a_then_b" else (by_b, by_a):
        recipient.deliver(att)
    view = recipient.view()

    assert view["selections"] == {"sel-2"}, "repair must not create a successor"
    assert not view["contradictions"], "equivalent duplicates are not a conflict"
    assert view["route"] == ROUTE_X
    # B repaired from adopted evidence alone: no selection, no allocation, and
    # nothing obtained from the interrupted device.
    assert (b.selections_made, b.allocations_made) == (0, 0)
    assert by_b.claim == by_a.claim


@pytest.mark.parametrize("retention", RETENTIONS)
@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("scheme_cls", SCHEMES, ids=lambda c: c.name)
@pytest.mark.parametrize("differing", ["route", "predecessor"])
def test_contradiction_under_one_selection(scheme_cls, order, retention, differing):
    """Case 2: differing content under one selection ID must stay visible.

    The reserved candidate depends on the recipient's retention policy; the
    separate candidate does not.
    """
    a, b, evidence = _interrupted_signing(scheme_cls)
    if differing == "route":
        by_b = b.attest(
            evidence.selection_id,
            route=dataclasses.replace(ROUTE_X, location="berth-core-y"),
        )
    else:
        by_b = b.attest(evidence.selection_id, predecessor_id="sel-0")
    by_a = a.attest(evidence.selection_id)
    assert by_a.claim != by_b.claim

    recipient = Recipient(retention)
    for att in (by_a, by_b) if order == "a_then_b" else (by_b, by_a):
        recipient.deliver(att)
    view = recipient.view()

    hidden = scheme_cls.reserves_identity and retention == IDENTITY_KEYED
    if hidden:
        # Disconfirming case for a reserved identity stored under its own
        # primary key: the later delivery displaces the earlier payload, so
        # the surviving claim depends on delivery order alone.
        assert not view["contradictions"]
        assert len(recipient.retained) == 1
        assert recipient.retained[0] is (by_b if order == "a_then_b" else by_a)
    else:
        assert set(view["contradictions"]) == {"sel-2"}
        assert view["route"] is None, "a contradiction must not be routed through"
        assert len(recipient.retained) == 2
