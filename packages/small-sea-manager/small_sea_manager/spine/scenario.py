"""The slice 1 story, as steps that only publish and deliver.

Alice removes Bob while Bob adds a device. Every device notices the conflict and
pauses Constitution publishing. Alice signs a reconciliation that keeps her
removal and rejects Bob's device; Carol accepts it and Bob refuses it.

Steps return nothing. What each device concludes is read from its own view,
so a test or the demo decides what to check or print between steps.
Step 8 writes a file rather than returning the bundle.
A step that a device refuses raises `device.Refused`.

The controller moves encoded bytes between devices. Its whole API is
`outbox(device)` and `deliver(device, bytes)`; withholding is not delivering,
and duplicating is delivering twice.
"""

from pathlib import Path

from . import encoding
from .device import Device

SEEDS = {label: bytes([n]) * 32 for n, label in enumerate(["a", "b", "c", "c2", "b2", "c3"], 1)}
KEY = {label: encoding.public_key(seed) for label, seed in SEEDS.items()}
GENESIS_TIME = 1_760_000_000


def _add(member, label):
    return {"op": "add_device", "member_id": member, "device_key": KEY[label], "label": label}


class Controller:
    """Fake delivery: what a device published, and handing bytes to a device."""

    def outbox(self, device: Device) -> list:
        return device.published()

    def deliver(self, device: Device, encoded: bytes):
        device.receive(encoded)


class Scenario:
    def __init__(self, directory):
        directory = Path(directory)
        self.genesis = encoding.make_genesis("00" * 32, [
            {"member_id": "alice", "name": "Alice"},
            {"member_id": "bob", "name": "Bob"},
            {"member_id": "carol", "name": "Carol"},
        ], [
            {"device_key": KEY["a"], "member_id": "alice", "label": "a"},
            {"device_key": KEY["b"], "member_id": "bob", "label": "b"},
            {"device_key": KEY["c"], "member_id": "carol", "label": "c"},
        ], time=GENESIS_TIME)
        self.origin = encoding.decode_genesis(self.genesis)["id"]
        self.directory = directory
        self.devices = {label: Device.create(directory / f"{label}.db", SEEDS[label], self.genesis)
                        for label in ["a", "b", "c"]}
        self.a, self.b, self.c = (self.devices[k] for k in ["a", "b", "c"])
        self.net = Controller()
        self.names = {self.origin: "G"}  # object ID -> story name, for display

    def id(self, name):
        return next(k for k, v in self.names.items() if v == name)

    def _publish(self, name, device, payload):
        self.names[encoding.decode_object(device.publish(payload))["id"]] = name

    def _send(self, name, source, *targets):
        """Deliver the named object from source's outbox to each target."""
        encoded = next(e for e in self.net.outbox(source)
                       if encoding.decode_object(e)["id"] == self.id(name))
        for target in targets:
            self.net.deliver(target, encoded)

    def step1_carol_adds_phone(self):
        """Carol adds phone c2 as P, delivered to everyone."""
        self._publish("P", self.c, _add("carol", "c2"))
        self._send("P", self.c, self.a, self.b)

    def step2_alice_removes_bob_and_bob_adds_device(self):
        """a publishes X (remove Bob) and b publishes Y (add b2); neither is delivered."""
        self._publish("X", self.a, {"op": "remove_member", "member_id": "bob"})
        self._publish("Y", self.b, _add("bob", "b2"))

    def step3a_carol_gets_bobs_device(self):
        self._send("Y", self.b, self.c)

    def step3b_everyone_sees_both(self):
        """c gets X; b gets X; a gets Y; c gets X a second time."""
        self._send("X", self.a, self.c, self.b)
        self._send("Y", self.b, self.a)
        self._send("X", self.a, self.c)

    def step4_carol_tries_tablet(self):
        """Carol tries to add tablet c3 while publishing is paused; c refuses."""
        self._publish("c3", self.c, _add("carol", "c3"))

    def step5_alice_reconciles(self):
        """a drafts, checks, signs, and follows R: adopt X, reject Y."""
        r = self.a.publish_reconciliation(
            [self.id("X"), self.id("Y")], [self.id("X")], [self.id("Y")])
        self.names[encoding.decode_object(r)["id"]] = "R"

    def step6a_bob_and_carol_get_r(self):
        self._send("R", self.a, self.b, self.c)

    def step6b_carol_accepts_bob_refuses(self):
        self.c.accept(self.id("R"))
        self.b.refuse(self.id("R"))

    def step7_carol_adds_tablet(self):
        """Carol adds c3 on top of R, and a follows it."""
        self._publish("c3", self.c, _add("carol", "c3"))
        self._send("c3", self.c, self.a)

    def step8_carol_exports(self):
        """c exports a bundle with tip c3 to `bundle_path`, for a verifier to check."""
        self.bundle_path = self.directory / "bundle.json"
        self.bundle_path.write_bytes(self.c.export_bundle(self.id("c3")))
