"""Multiple-location step 3: local read stops, replay, and discovery.

A standalone model, not a probe. It runs no runtime code and shares no
vocabulary with the single-route models: those represent route succession and
a receiving teammate's pause, and this candidate has neither. It is deliberately
smaller than they are, because the questions left after the runtime probe are
smaller. See ../plan.md "Model only the stop and replay schedules" and
../probes/probe_multiple_locations.py for everything the runtime already
answers.

Modeled state, and nothing else:

  - which locations an actor knows about, and the announcement that made each
    one known
  - which locations an actor has decided to stop reading, and to stop writing
  - which publications an actor has retrieved, and where the world's stores
    actually hold them
  - one write destination per actor

There is no simulator, no polling, no clock, no migration engine, and no
signature checking: every announcement here is already authenticated, because
authentication is a boundary the runtime enforces and this model has nothing to
add to it. Retrieval likewise stands for "fetched and verified"; integration of
retrieved histories is a separate decision the model does not represent.

The schedules are the ninth through eleventh in ../plan.md, plus the two
write-placement rows that must not collapse into them:

  9.  T stops reading X, then receives X's announcement again.
  10. An offline sibling publishes valid work at stopped X.
  11. T knows only X while future work moves to Y.
  8.  A delayed X announcement arrives after Y was chosen for writes.
  14. T stops uploading to X while the provider keeps every old byte.

Two controls are included because the plan requires the assertions to be shown
catching the behavior they forbid: a reader whose stop is cleared by replay,
and a reader that stops at the first location that answers.

What the model assumes rather than establishes: that a stop, once taken, is
still held later in the same run. Whether it survives restart, restoration of
older local state or redelivery after a crash is plan step 7's question, and
nothing here is evidence about it.
"""
import dataclasses

import pytest

# --- The world --------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Announcement:
    """An authenticated claim that a berth's work may be found at `location`.

    `announcement_id` orders minting, not truth: a later id says only that the
    claim was signed later, which is what the runtime selector sorts on.
    """

    location: str
    announcement_id: int
    signer: str


@dataclasses.dataclass(frozen=True)
class Publication:
    publication_id: str
    author: str


class Store:
    """One cloud location: what it holds, and whether it answers at all."""

    def __init__(self, location):
        self.location = location
        self.contents = []
        self.available = True

    def put(self, publication):
        self.contents.append(publication)
        return publication

    def get(self):
        if not self.available:
            raise ConnectionError(self.location)
        return list(self.contents)


class World:
    """The stores that exist. No actor may enumerate it."""

    def __init__(self, *locations):
        self.stores = {location: Store(location) for location in locations}

    def store(self, location):
        return self.stores[location]


# --- Actors -----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ReadOutcome:
    """One read attempt, reported without a completeness claim.

    `skipped` names the locations a held stop excluded, so a stop stays visible
    in the outcome rather than becoming indistinguishable from ignorance.
    """

    retrieved: frozenset
    attempted: frozenset
    skipped: frozenset
    unavailable: frozenset

    #: No read attempt establishes that it saw every publication that exists,
    #: at the locations it skipped or at any location nobody told this actor
    #: about.
    complete = False


class Actor:
    """A device that knows locations, reads some of them, and writes to one."""

    def __init__(self, name):
        self.name = name
        self.known = {}
        self.read_stops = {}
        self.write_stops = set()
        self.held = set()
        self.write_destination = None

    # -- knowledge --------------------------------------------------------

    def learn(self, announcement):
        """Record a location. Hearing about it is not a decision about it.

        A replayed or delayed announcement updates what this actor knows and
        touches neither the read stops nor the write destination.
        """
        previous = self.known.get(announcement.location)
        if previous is None or announcement.announcement_id > previous.announcement_id:
            self.known[announcement.location] = announcement
        return self

    @property
    def read_set(self):
        return frozenset(self.known) - frozenset(self.read_stops)

    # -- decisions --------------------------------------------------------

    def stop_reading(self, location, *, reason):
        """Stop reading a location, keeping the evidence the choice was made.

        The announcement id held at the time is retained so that redelivery of
        that same announcement is recognizable as replay rather than as news.
        """
        seen = self.known.get(location)
        self.read_stops[location] = {
            "reason": reason,
            "announcement_id": None if seen is None else seen.announcement_id,
        }
        return self

    def resume_reading(self, location):
        """A deliberate request to read a location again."""
        self.read_stops.pop(location, None)
        return self

    def choose_writes(self, location):
        self.write_destination = location
        return self

    def stop_writing(self, location):
        self.write_stops.add(location)
        if self.write_destination == location:
            self.write_destination = None
        return self

    # -- I/O --------------------------------------------------------------

    def read(self, world):
        attempted, unavailable, retrieved = set(), set(), set()
        for location in sorted(self.read_set):
            attempted.add(location)
            try:
                found = world.store(location).get()
            except ConnectionError:
                unavailable.add(location)
                continue
            for publication in found:
                self.held.add(publication.publication_id)
                retrieved.add(publication.publication_id)
        return ReadOutcome(
            retrieved=frozenset(retrieved),
            attempted=frozenset(attempted),
            skipped=frozenset(self.read_stops) & frozenset(self.known),
            unavailable=frozenset(unavailable),
        )

    def inspect(self, world, location):
        """A deliberate look at one known location, stop or no stop.

        Distinct from `read`: it is not a retry, it does not resume the
        location, and it does not make it a write destination.
        """
        if location not in self.known:
            return {"observed": None, "outcome": "unknown_location"}
        try:
            found = world.store(location).get()
        except ConnectionError:
            return {"observed": None, "outcome": "unavailable"}
        for publication in found:
            self.held.add(publication.publication_id)
        return {"observed": frozenset(p.publication_id for p in found), "outcome": "read"}

    def publish(self, world, publication_id):
        if self.write_destination is None or self.write_destination in self.write_stops:
            return None
        return world.store(self.write_destination).put(
            Publication(publication_id, self.name)
        )

    # -- what the actor may say -------------------------------------------

    def stop_report(self, location):
        """The claims a stop supports, and the ones it does not."""
        return {
            "location": location,
            "this_actor_reads_it": location in self.read_set,
            "this_actor_uploads_to_it": (
                self.write_destination == location and location not in self.write_stops
            ),
            "other_actors_stopped": "unknown",
            "work_may_be_missed": True,
            "provider_erased_data": "not claimed",
            "signer_revoked": "not claimed",
            "location_permanently_silent": "not claimed",
        }


class ReplayForgetsStop(Actor):
    """Control: an actor whose stop is cleared by hearing the location again."""

    def learn(self, announcement):
        self.read_stops.pop(announcement.location, None)
        return super().learn(announcement)


class FirstSuccessReader(Actor):
    """Control: an actor that stops at the first location that answers."""

    def read(self, world):
        attempted, unavailable, retrieved = set(), set(), set()
        for location in sorted(self.read_set):
            attempted.add(location)
            try:
                found = world.store(location).get()
            except ConnectionError:
                unavailable.add(location)
                continue
            for publication in found:
                self.held.add(publication.publication_id)
                retrieved.add(publication.publication_id)
            break
        return ReadOutcome(
            retrieved=frozenset(retrieved),
            attempted=frozenset(attempted),
            skipped=frozenset(self.read_stops) & frozenset(self.known),
            unavailable=frozenset(unavailable),
        )


# --- Schedules --------------------------------------------------------------

ANN_X = Announcement("X", 1, signer="A")
ANN_Y = Announcement("Y", 2, signer="B")
#: The same location announced again, later. Redelivery of the old claim and a
#: freshly minted one for X are both replay as far as a read stop is concerned.
ANN_X_AGAIN = Announcement("X", 3, signer="A")

READERS = [Actor, ReplayForgetsStop]


def _reader_knowing_both(reader_class=Actor):
    world = World("X", "Y")
    world.store("X").put(Publication("x-1", "A"))
    world.store("Y").put(Publication("y-1", "B"))
    reader = reader_class("T").learn(ANN_X).learn(ANN_Y)
    return world, reader


@pytest.mark.parametrize("replayed", [ANN_X, ANN_X_AGAIN], ids=["same", "reminted"])
@pytest.mark.parametrize("reader_class", READERS, ids=lambda c: c.__name__)
def test_a_replayed_announcement_does_not_undo_a_read_stop(reader_class, replayed):
    """Schedule 9, with the control that shows the assertion has teeth."""
    world, reader = _reader_knowing_both(reader_class)
    reader.read(world)
    reader.stop_reading("X", reason="the person chose to stop reading X")

    reader.learn(replayed)

    if reader_class is ReplayForgetsStop:
        # Disconfirming case: redelivery silently reverses the decision, and
        # nothing distinguishes the resumed location from one never stopped.
        assert "X" in reader.read_set
        assert reader.read_stops == {}
        return

    assert reader.read_set == {"Y"}
    assert reader.read_stops["X"]["announcement_id"] == ANN_X.announcement_id
    outcome = reader.read(world)
    assert outcome.attempted == {"Y"}
    assert outcome.skipped == {"X"}

    # The stop is this actor's. A sibling that never stopped still reads X, and
    # the report claims nothing about anyone else.
    sibling = Actor("B").learn(ANN_X).learn(ANN_Y)
    assert sibling.read(world).attempted == {"X", "Y"}
    report = reader.stop_report("X")
    assert report["this_actor_reads_it"] is False
    assert report["other_actors_stopped"] == "unknown"
    assert report["location_permanently_silent"] == "not claimed"


def test_a_deliberate_resume_is_not_a_retry():
    """Reading again is not resuming; resuming is a separate request."""
    world, reader = _reader_knowing_both()
    reader.stop_reading("X", reason="stopped")

    assert reader.read(world).attempted == {"Y"}
    assert reader.read(world).attempted == {"Y"}

    reader.resume_reading("X")
    assert reader.read(world).attempted == {"X", "Y"}


def test_work_published_at_a_stopped_location_is_missed_but_still_valid():
    """Schedule 10: an offline sibling publishes at X after T stopped reading.

    The publication is not invalidated by the stop and is not retracted by it.
    What the stop costs is discovery, and the model says so rather than
    reporting a complete read.
    """
    world, reader = _reader_knowing_both()
    reader.read(world)
    reader.stop_reading("X", reason="stopped")

    offline_sibling = Actor("B").learn(ANN_X).choose_writes("X")
    late = offline_sibling.publish(world, "x-2")
    assert late in world.store("X").contents

    outcome = reader.read(world)
    assert "x-2" not in outcome.retrieved
    assert "x-2" not in reader.held
    assert outcome.complete is False
    assert reader.stop_report("X")["work_may_be_missed"] is True

    # Manual inspection recovers it while the location is still available, and
    # is not a resumption: the read set is unchanged afterwards.
    found = reader.inspect(world, "X")
    assert found["outcome"] == "read"
    assert "x-2" in found["observed"]
    assert "x-2" in reader.held
    assert reader.read_set == {"Y"}

    # And when the location is gone, the outcome is a limitation, not a
    # recovery protocol.
    world.store("X").available = False
    unreachable = Actor("U").learn(ANN_X).learn(ANN_Y)
    unreachable.stop_reading("X", reason="stopped")
    assert unreachable.inspect(world, "X") == {"observed": None, "outcome": "unavailable"}


@pytest.mark.parametrize("delivery", ["none", "through_x", "out_of_band"])
def test_a_reader_knowing_only_x_learns_y_only_through_a_delivery(delivery):
    """Schedule 11: trying every known location does not discover Y.

    The forwarding case is a candidate path, not an accepted representation. It
    is modeled as an announcement that happens to be retrievable through X: it
    confers no trust the announcement did not already carry, and it does not
    retire X.
    """
    world = World("X", "Y")
    world.store("Y").put(Publication("y-1", "B"))
    reader = Actor("T").learn(ANN_X)

    assert reader.read_set == {"X"}
    assert reader.read(world).retrieved == frozenset()

    if delivery == "none":
        # No global inventory: Y exists and stays undiscoverable.
        assert "Y" not in reader.known
        assert reader.read(world).attempted == {"X"}
        assert reader.stop_report("Y")["this_actor_reads_it"] is False
        return

    if delivery == "through_x":
        world.store("X").put(Publication("forward-to-Y", "A"))
        assert "forward-to-Y" in reader.inspect(world, "X")["observed"]
    reader.learn(ANN_Y)

    assert reader.read_set == {"X", "Y"}
    assert "y-1" in reader.read(world).retrieved
    # Learning Y neither stops X nor moves writes to Y.
    assert reader.read_stops == {}
    assert reader.write_destination is None


def test_read_stops_and_write_placement_are_separate_decisions():
    """Schedules 8 and 14: reads and uploads are decided independently."""
    world, reader = _reader_knowing_both()
    reader.choose_writes("Y")

    # A delayed announcement for X arrives after Y was chosen for writes.
    reader.learn(ANN_X_AGAIN)
    assert reader.write_destination == "Y"
    reader.publish(world, "t-1")
    assert [p.publication_id for p in world.store("Y").contents] == ["y-1", "t-1"]
    assert world.store("X").contents == [Publication("x-1", "A")]

    # Stopping reads at X leaves the write destination alone, and stopping
    # uploads to Y leaves the read set alone.
    reader.stop_reading("X", reason="stopped")
    assert reader.write_destination == "Y"
    reader.stop_writing("Y")
    assert reader.read_set == {"Y"}
    assert reader.publish(world, "t-2") is None

    # A write stop describes this actor's uploads and nothing else: the
    # provider keeps every byte, and a sibling can still publish there.
    assert [p.publication_id for p in world.store("Y").contents] == ["y-1", "t-1"]
    sibling = Actor("B").learn(ANN_Y).choose_writes("Y")
    assert sibling.publish(world, "y-2") is not None
    report = reader.stop_report("Y")
    assert report["this_actor_uploads_to_it"] is False
    assert report["provider_erased_data"] == "not claimed"
    assert report["signer_revoked"] == "not claimed"


def test_first_success_reading_loses_the_other_locations_work():
    """The control the plan requires for reading more than one location.

    Both locations answer and each holds work the other does not. An actor that
    returns after the first success reports an outcome that looks successful
    and is missing half the work; the assertions above would not catch it
    without this comparison.
    """
    world, reader = _reader_knowing_both()
    outcome = reader.read(world)
    assert outcome.retrieved == {"x-1", "y-1"}
    assert outcome.attempted == {"X", "Y"}

    _control_world, control = _reader_knowing_both(FirstSuccessReader)
    control_outcome = control.read(_control_world)
    assert control_outcome.retrieved == {"x-1"}
    assert control_outcome.attempted == {"X"}
    assert control_outcome.unavailable == frozenset()
    assert "y-1" not in control.held
