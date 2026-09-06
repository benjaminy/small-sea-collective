"""Move 6 model: what binds a selection and its attestations to exact route content?

A standalone model like the earlier four. It runs no runtime code and uses no
runtime schema, and it deliberately does not assume today's table boundaries:
the question is which values a signer reads, not which row they sit on.

Moves 1 through 5 assumed complete route content and assumed provider effects
were already done. This move drops both assumptions. Two counterexamples from
the evidence table drive it:

  account-only change    A signs a selection while account metadata says one
                         thing; the metadata changes; a sibling repairs the
                         same selection and signs something else. Both
                         signatures are valid and they disagree about the route.
  differing locator      the provider finalizes a locator other than the one
                         requested, and at first only one device knows it.

Three bindings are compared, distinguished by what a signer reads:

  FROZEN_PROJECTION  the selection record carries complete route content, and
                     signing reads only the record.
  REFERENCE_BINDING  the record names an allocation, and signing resolves
                     protocol, endpoint, locator and account state from
                     whatever the mutable rows say at that moment.
  CONTENT_DIGEST     the record names an allocation and fixes a digest over the
                     complete content. Signing requires content the device
                     actually holds whose digest matches, and is impossible
                     otherwise.

The route fields are the ones ../plan.md question 3 names: protocol, endpoint,
the final locator, and the account state a route depends on. `location` is the
provider-finalized field and `account_region` is the independently changing one.

Assumptions, which the model asserts rather than establishes:

  - Account metadata is participant-level state that reaches every device; the
    schedules that need it stale say so explicitly.
  - A published selection record reaches siblings; content held only on the
    device that minted it does not, unless a schedule delivers it.
  - Signatures are authentic. A device signs what it reads, never what it wants.
  - Everything the Move 3 model assumes about counters and private history.

Model success says nothing about whether the runtime supplies those guarantees.
"""
import dataclasses
import hashlib
import itertools

import pytest

from model_succession_branches import (
    COUNTER_AND_PREDECESSOR,
    Attestation,
    Route,
    Teammate,
)

FROZEN_PROJECTION = "frozen-projection"
REFERENCE_BINDING = "reference-binding"
CONTENT_DIGEST = "content-digest"
BINDINGS = [FROZEN_PROJECTION, REFERENCE_BINDING, CONTENT_DIGEST]

PROTOCOL = "s3"
ENDPOINT = "https://minio.example/"
REQUESTED = "berth-x-requested"
FINAL = "berth-x-final"
HOME = "us-west"
MOVED = "eu-central"

_MINTED = itertools.count()


# --- Mutable state the signer might read ------------------------------------


class Account:
    """Participant account metadata: a row that changes on its own schedule."""

    def __init__(self, region=HOME):
        self.region = region


class Allocation:
    """The allocation row, including the locator the provider settled on."""

    def __init__(self, allocation_id, location=None):
        self.allocation_id = allocation_id
        self.protocol = PROTOCOL
        self.endpoint = ENDPOINT
        self.location = location


class Provider:
    """Materializes storage, and may finalize a locator other than the request."""

    def __init__(self):
        self.materialized = set()
        self.requests = 0

    def materialize(self, requested, final=None):
        self.requests += 1
        location = requested if final is None else final
        self.materialized.add(location)
        return location

    def exists(self, location):
        return location in self.materialized


# --- The record ------------------------------------------------------------


def digest_of(route):
    return hashlib.sha256(repr(dataclasses.astuple(route)).encode()).hexdigest()[:16]


@dataclasses.dataclass(frozen=True)
class Record:
    """A published selection record under one binding.

    Every binding carries selection identity, succession and the allocation it
    concerns. They differ only in what else is fixed here rather than read at
    signing time.
    """

    selection_id: str
    counter: int
    predecessor_id: str | None
    binding: str
    allocation_id: str
    route: Route | None = None
    digest: str | None = None


# --- Devices ---------------------------------------------------------------


class Signer:
    """A device that mints selection records, adopts them and attests to them."""

    def __init__(self, name, binding, allocations, account):
        self.name = name
        self.binding = binding
        self.allocations = allocations
        self.account = account
        self.records = {}
        self.content = {}

    def route_now(self, allocation_id):
        """The route this device's mutable rows describe at this moment."""
        allocation = self.allocations[allocation_id]
        return Route(
            allocation.protocol,
            allocation.endpoint,
            allocation.location,
            self.account.region,
        )

    def mint(self, selection_id, counter, predecessor_id, allocation_id):
        route = self.route_now(allocation_id)
        record = Record(
            selection_id=selection_id,
            counter=counter,
            predecessor_id=predecessor_id,
            binding=self.binding,
            allocation_id=allocation_id,
            route=route if self.binding == FROZEN_PROJECTION else None,
            digest=digest_of(route) if self.binding == CONTENT_DIGEST else None,
        )
        self.records[selection_id] = record
        self.content[selection_id] = route
        return record

    def adopt(self, record, content=None):
        """Adopt a published record, and separately whatever content arrived."""
        self.records[record.selection_id] = record
        if content is not None:
            self.content[record.selection_id] = content

    def attest(self, selection_id):
        """Sign what this device reads, or return None if it cannot read it.

        Returning None is not a failure of the model: refusing to sign is the
        honest answer when the binding demands content the device does not have.
        """
        record = self.records[selection_id]
        if record.binding == FROZEN_PROJECTION:
            route = record.route
        elif record.binding == REFERENCE_BINDING:
            route = self.route_now(record.allocation_id)
        else:
            route = self.content.get(selection_id)
            if route is None or digest_of(route) != record.digest:
                return None
        return Attestation(
            attestation_id=f"att-{self.name}-{next(_MINTED)}",
            signer=self.name,
            selection_id=record.selection_id,
            counter=record.counter,
            predecessor_id=record.predecessor_id,
            route=route,
        )


# --- The schedules ---------------------------------------------------------


def _selected(binding, finalize_before_mint=True):
    """A materializes storage and mints one selection for it.

    `finalize_before_mint` is the ordering question: whether the record may be
    created before the provider has settled the locator it names.
    """
    provider = Provider()
    account = Account()
    allocation = Allocation("alloc-1")
    a = Signer("A", binding, {"alloc-1": allocation}, account)

    if finalize_before_mint:
        allocation.location = provider.materialize(REQUESTED, final=FINAL)
        record = a.mint("sel-x", 2, "sel-p", "alloc-1")
    else:
        allocation.location = REQUESTED
        record = a.mint("sel-x", 2, "sel-p", "alloc-1")
        allocation.location = provider.materialize(REQUESTED, final=FINAL)
    return provider, account, allocation, a, record


def _sibling(binding, account, record, allocation, deliver_content=None):
    """A second device holding the published record and its own rows."""
    b = Signer("B", binding, {allocation.allocation_id: allocation}, account)
    b.adopt(record, content=deliver_content)
    return b


def _delivered(*attestations):
    teammate = Teammate(COUNTER_AND_PREDECESSOR)
    for attestation in attestations:
        teammate.deliver(attestation)
    return teammate


# --- The account-only change -----------------------------------------------


@pytest.mark.parametrize("binding", BINDINGS)
def test_an_account_change_must_not_change_what_an_earlier_selection_means(binding):
    """The discriminator: sibling repair after account metadata moves.

    A signs the selection while the account says `us-west`. The account row then
    moves to `eu-central`, and B repairs the same selection. Reading the account
    at signing time makes the repair contradict the selection it was repairing,
    with two valid signatures and no way for either device to be at fault.
    """
    _provider, account, allocation, a, record = _selected(binding)
    content = a.content["sel-x"]
    b = _sibling(binding, account, record, allocation, deliver_content=content)

    first = a.attest("sel-x")
    account.region = MOVED
    repair = b.attest("sel-x")
    teammate = _delivered(first, repair)

    if binding == REFERENCE_BINDING:
        assert repair.route.account_region == MOVED
        assert repair.claim != first.claim
        assert set(teammate.contradictions()) == {"sel-x"}
        assert teammate.route() is None, "repair destroyed the route it repaired"
    else:
        assert repair.claim == first.claim, "repair restates the selection"
        assert not teammate.contradictions()
        assert teammate.route() == first.route


def test_the_digest_turns_a_missing_content_path_into_a_refusal():
    """What the digest buys over the frozen projection, and what it costs.

    A record naming an allocation plus a digest keeps a sibling from signing
    substituted content, which is the same guarantee the frozen projection gives.
    The difference is availability: the record alone does not carry the content,
    so repair stalls until the content arrives by some other path.
    """
    _provider, account, allocation, a, record = _selected(CONTENT_DIGEST)
    without = _sibling(CONTENT_DIGEST, account, record, allocation)
    assert without.attest("sel-x") is None, "no content, no attestation"

    account.region = MOVED
    assert without.attest("sel-x") is None, "and the change does not unblock it"

    with_content = _sibling(
        CONTENT_DIGEST, account, record, allocation, deliver_content=a.content["sel-x"]
    )
    assert with_content.attest("sel-x").claim == a.attest("sel-x").claim


@pytest.mark.parametrize("binding", BINDINGS)
def test_repair_from_the_published_record_alone(binding):
    """Which binding makes the published record sufficient for sibling repair.

    B's allocation row is stale — it never saw the finalized locator — and no
    content was delivered. This is the ledger's repair property meeting the
    binding question: a sibling must be able to finish interrupted work from
    durable shared evidence without materializing storage again.
    """
    provider, account, _allocation, a, record = _selected(binding)
    stale = Allocation("alloc-1", location=REQUESTED)
    b = _sibling(binding, account, record, stale)
    repair = b.attest("sel-x")

    if binding == FROZEN_PROJECTION:
        assert repair.claim == a.attest("sel-x").claim
        assert provider.exists(repair.route.location)
    elif binding == REFERENCE_BINDING:
        assert repair.route.location == REQUESTED
        assert not provider.exists(repair.route.location), (
            "a stale row names storage that does not exist"
        )
    else:
        assert repair is None, "correct, but repair waits on a second delivery"


# --- Provider finalization -------------------------------------------------


@pytest.mark.parametrize("binding", BINDINGS)
@pytest.mark.parametrize("finalize_before_mint", [True, False])
def test_a_record_may_not_fix_a_locator_the_provider_has_not_settled(
    binding, finalize_before_mint
):
    """The ordering rule, and why no representation replaces it.

    Minting before the provider settles the locator fixes the requested value.
    Freezing it and digesting it both preserve a value that was never true;
    resolving it later tracks the correction, but only because late binding is
    willing to change what a selection means, which is the property the account
    schedule rules out. So the two cannot be had at once, and the cheaper side
    is a rule about when a record may be created.
    """
    provider, _account, _allocation, a, _record = _selected(
        binding, finalize_before_mint=finalize_before_mint
    )
    attested = a.attest("sel-x")

    if finalize_before_mint:
        assert attested.route.location == FINAL
        assert provider.exists(attested.route.location)
    elif binding == REFERENCE_BINDING:
        assert attested.route.location == FINAL, "late binding tracks the correction"
        assert provider.exists(attested.route.location)
    else:
        assert attested.route.location == REQUESTED
        assert not provider.exists(attested.route.location), (
            "the record fixed a locator the provider never settled on"
        )


# --- Merge -----------------------------------------------------------------


def test_union_merge_of_rows_and_records_invents_a_route_nobody_signed():
    """Why whole-row merge cannot rescue a reference binding.

    A signs at `us-west`. Merging A's record set with a peer's account row is a
    union of two independently valid states, and the route derived from the
    merge is a third thing that neither device ever chose or signed. The frozen
    record is closed under the same merge, because it reads nothing.
    """
    provider, _account, allocation, a, record = _selected(REFERENCE_BINDING)
    signed = a.attest("sel-x")
    assert signed.route.account_region == HOME

    merged = Signer("merged", REFERENCE_BINDING, {"alloc-1": allocation}, Account(MOVED))
    merged.adopt(record)
    assert merged.attest("sel-x").route.account_region == MOVED
    assert merged.attest("sel-x").claim != signed.claim

    _p2, _account2, allocation2, a2, record2 = _selected(FROZEN_PROJECTION)
    frozen_merged = Signer(
        "merged", FROZEN_PROJECTION, {"alloc-1": allocation2}, Account(MOVED)
    )
    frozen_merged.adopt(record2)
    assert frozen_merged.attest("sel-x").claim == a2.attest("sel-x").claim


# --- Content equality is not selection identity ----------------------------


def test_returning_to_a_location_is_a_new_selection_not_the_old_one():
    """Binding runs one way: a selection fixes content; content names no selection.

    X at counter 2, Y at 3, then a deliberate return to X's exact route at 4.
    The first and third records are field-for-field identical in route content
    and are different selections. A recipient that treated equal content as the
    same selection would let the delayed Y outrank the human's return.
    """
    provider, account, allocation, a, first = _selected(FROZEN_PROJECTION)
    other = Allocation("alloc-2", location="berth-y")
    a.allocations["alloc-2"] = other
    a.mint("sel-y", 3, "sel-x", "alloc-2")
    again = a.mint("sel-x-again", 4, "sel-y", "alloc-1")

    assert again.route == first.route, "identical content"
    assert again.selection_id != first.selection_id

    # Y arrives last, after both records naming X's content.
    teammate = _delivered(
        a.attest("sel-x"), a.attest("sel-x-again"), a.attest("sel-y")
    )
    assert not teammate.contradictions(), "equal content under distinct selections"
    assert len(teammate.selections) == 3, "no record is folded into another"
    routed = max(teammate.selections.values(), key=lambda att: att.counter)
    assert routed.selection_id == "sel-x-again"
    assert teammate.route() == first.route
    assert provider.exists(first.route.location)
