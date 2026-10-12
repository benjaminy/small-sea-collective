"""One device's spine store and the Manager decisions that sit on it.

Storage is one SQLite file per device (README section 2):
- spine_object: every received or published object that passed core checks,
  valid under policy or not; the first envelope for an ID wins.
- spine_retained_copy: which reconciliation carries the first envelope for each
  event, so a standalone copy may be deleted and still found.
- spine_choice: the head this device last followed, and the reconciliations its
  owner refused. These are the only remembered choices.
- spine_receipt: what the device concluded at each arrival, for display only.
  No decision reads it.
- spine_published: what this device published, in order; its outbox.
- spine_identity: this device's private key and the pinned genesis bytes.
  The key is stored in the clear; this is a demo store.

Nothing stores tips or a pause: `view()` recomputes them from the objects and the
two choices. After every arrival and on every `view()`, the device records the
followed head when the view says "followed"; it never moves it to a conflict's
base.

Constitution publishing is guarded by the view's `may_publish`; apps are not
affected. Every publish runs the same checks a receiver would and refuses an
object that would not be valid here.
"""

import os
import time

from sqlalchemy import create_engine, text

from . import encoding, history, reconcile, view as view_module

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS spine_object (
        object_id TEXT PRIMARY KEY, kind TEXT NOT NULL, encoded BLOB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS spine_retained_copy (
        object_id TEXT NOT NULL, container_id TEXT NOT NULL,
        PRIMARY KEY (object_id, container_id))""",
    """CREATE TABLE IF NOT EXISTS spine_choice (
        object_id TEXT PRIMARY KEY,
        choice TEXT NOT NULL CHECK (choice IN ('followed', 'refused')))""",
    """CREATE TABLE IF NOT EXISTS spine_receipt (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, object_id TEXT,
        disposition TEXT NOT NULL, reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS spine_published (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, object_id TEXT NOT NULL UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS spine_identity (
        only_row INTEGER PRIMARY KEY CHECK (only_row = 1),
        seed BLOB NOT NULL, genesis BLOB NOT NULL)""",
]


class Refused(Exception):
    """The Manager will not publish or record this choice; the message says why."""


class Device:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path}")
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT seed, genesis FROM spine_identity")).one()
        self.seed, self.genesis_bytes = bytes(row.seed), bytes(row.genesis)
        self.key = encoding.public_key(self.seed)
        self.origin = encoding.decode_genesis(self.genesis_bytes)["id"]

    @classmethod
    def create(cls, path, seed: bytes, genesis_bytes: bytes) -> "Device":
        """Make a new store pinned to this genesis; refuses an existing file."""
        if os.path.exists(path):
            raise FileExistsError(path)
        encoding.decode_genesis(genesis_bytes)
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            for statement in _SCHEMA:
                conn.execute(text(statement))
            conn.execute(text("INSERT INTO spine_identity VALUES (1, :seed, :genesis)"),
                         {"seed": seed, "genesis": genesis_bytes})
        engine.dispose()
        return cls(path)

    def close(self):
        self.engine.dispose()

    # ---- storage ----

    def _objects(self, conn):
        return [bytes(r.encoded) for r in conn.execute(
            text("SELECT encoded FROM spine_object ORDER BY object_id"))]

    def _choices(self, conn):
        rows = conn.execute(text("SELECT object_id, choice FROM spine_choice")).all()
        followed = next((r.object_id for r in rows if r.choice == "followed"), None)
        return followed, sorted(r.object_id for r in rows if r.choice == "refused")

    def _set_followed(self, conn, object_id):
        conn.execute(text("DELETE FROM spine_choice WHERE choice = 'followed'"))
        conn.execute(text("INSERT INTO spine_choice VALUES (:id, 'followed')"),
                     {"id": object_id})

    def _receipt(self, conn, object_id, disposition, reason=None):
        conn.execute(text(
            "INSERT INTO spine_receipt (object_id, disposition, reason) VALUES (:id, :d, :r)"),
            {"id": object_id, "d": disposition, "r": reason})

    def _get(self, conn, object_id):
        """Encoded bytes for an ID: the standalone object, else a retained copy."""
        row = conn.execute(text("SELECT encoded FROM spine_object WHERE object_id = :id"),
                           {"id": object_id}).one_or_none()
        if row is not None:
            return bytes(row.encoded)
        for r in conn.execute(text(
                "SELECT container_id FROM spine_retained_copy WHERE object_id = :id "
                "ORDER BY container_id"), {"id": object_id}):
            container = self._get(conn, r.container_id)
            for copy in encoding.decode_canonical(container)["body"]["payload"]["copies"]:
                if encoding.decode_object(bytes.fromhex(copy))["id"] == object_id:
                    return bytes.fromhex(copy)
        if object_id == self.origin:
            return self.genesis_bytes
        return None

    def _store(self, conn, encoded: bytes):
        """Store one decoded object. Returns (status, reason): "stored", "duplicate",
        or "refused" for an envelope conflict. Copies inside a reconciliation are
        indexed when they match the first envelope; a copy whose envelope differs
        is logged as an envelope conflict and does not change the container's fate."""
        obj = encoding.decode_object(encoded)
        held = self._get(conn, obj["id"])
        if held is not None:
            return ("duplicate", None) if held == encoded else ("refused", "envelope conflict")
        conn.execute(text("INSERT INTO spine_object VALUES (:id, :kind, :encoded)"),
                     {"id": obj["id"], "kind": obj["body"]["kind"], "encoded": encoded})
        self._index_copies(conn, obj["id"], encoded)
        return "stored", None

    def _index_copies(self, conn, container_id, encoded):
        payload = encoding.decode_canonical(encoded)["body"].get("payload", {})
        for copy_hex in payload.get("copies", []):
            copy = bytes.fromhex(copy_hex)
            copy_id = encoding.decode_object(copy)["id"]
            held = self._get(conn, copy_id)
            if held is not None and held != copy:
                self._receipt(conn, copy_id, "refused",
                              f"envelope conflict (copy inside {container_id})")
                continue
            conn.execute(text("INSERT OR IGNORE INTO spine_retained_copy VALUES (:id, :c)"),
                         {"id": copy_id, "c": container_id})
            self._index_copies(conn, copy_id, copy)

    # ---- receiving ----

    def receive(self, encoded: bytes) -> dict:
        """Take bytes from a peer. Returns this arrival's receipt:
        {"object_id", "disposition", "reason"}, where disposition is "refused",
        "duplicate", or the object's status in the new view (valid, invalid, pending)."""
        with self.engine.begin() as conn:
            try:
                obj = encoding.decode_object(encoded)
            except encoding.EncodingError as e:
                receipt = {"object_id": None, "disposition": "refused", "reason": f"core: {e}"}
            else:
                receipt = self._receive_decoded(conn, obj, encoded)
            self._receipt(conn, receipt["object_id"], receipt["disposition"], receipt["reason"])
        return receipt

    def _receive_decoded(self, conn, obj, encoded):
        oid = obj["id"]
        if obj["body"]["kind"] == "genesis":
            if oid == self.origin:
                return {"object_id": oid, "disposition": "duplicate", "reason": None}
            return {"object_id": oid, "disposition": "refused",
                    "reason": "not the pinned genesis"}
        if obj["body"]["origin"] != self.origin:
            return {"object_id": oid, "disposition": "refused",
                    "reason": "origin is not the pinned genesis"}
        stored, reason = self._store(conn, encoded)
        if stored != "stored":
            return {"object_id": oid, "disposition": stored, "reason": reason}
        found = self._refresh(conn)["objects"][oid]
        return {"object_id": oid, "disposition": found["status"], "reason": found["reason"]}

    def receipts(self) -> list:
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(
                text("SELECT object_id, disposition, reason FROM spine_receipt ORDER BY seq"))]

    # ---- the view and the owner's choices ----

    def _view(self, conn, extra=()):
        followed, refused = self._choices(conn)
        return view_module.compute_view(self._objects(conn) + list(extra),
                                        self.genesis_bytes, followed, refused)

    def _refresh(self, conn):
        """The view; when it says "followed", record its head as the followed head."""
        v = self._view(conn)
        if v["classification"] == "followed" and self._choices(conn)[0] != v["heads"][0]:
            self._set_followed(conn, v["heads"][0])
        return v

    def view(self) -> dict:
        """The device's view (see view.compute_view)."""
        with self.engine.begin() as conn:
            return self._refresh(conn)

    def published(self) -> list:
        """Encoded objects this device published, oldest first."""
        with self.engine.connect() as conn:
            ids = [r.object_id for r in conn.execute(
                text("SELECT object_id FROM spine_published ORDER BY seq"))]
            return [self._get(conn, object_id) for object_id in ids]

    def followed(self):
        with self.engine.connect() as conn:
            return self._choices(conn)[0]

    def accept(self, r_id: str):
        """The owner accepts a contested reconciliation: the device follows it."""
        with self.engine.begin() as conn:
            if r_id not in self._view(conn)["contested"]:
                raise Refused("not a reconciliation the owner must choose about")
            self._set_followed(conn, r_id)

    def refuse(self, r_id: str):
        """The owner refuses a contested reconciliation: it and everything built
        on it become invalid on this device."""
        with self.engine.begin() as conn:
            if r_id not in self._view(conn)["contested"]:
                raise Refused("not a reconciliation the owner must choose about")
            conn.execute(text("INSERT INTO spine_choice VALUES (:id, 'refused')"), {"id": r_id})

    # ---- publishing ----

    def _publish(self, conn, encoded: bytes):
        """Check the object as a receiver would, then store it and follow it."""
        oid = encoding.decode_object(encoded)["id"]
        found = self._view(conn, [encoded])["objects"][oid]
        if found["status"] != "valid":
            raise Refused(f"would not be valid here: {found['reason']}")
        stored, reason = self._store(conn, encoded)
        if stored != "stored":
            raise Refused(reason or "already published")
        self._receipt(conn, oid, "valid", "published here")
        conn.execute(text("INSERT INTO spine_published (object_id) VALUES (:id)"), {"id": oid})
        self._set_followed(conn, oid)
        return encoded

    def publish(self, payload: dict) -> bytes:
        """Sign an ordinary event on the single head. Refused while Constitution
        publishing is paused, or when the operation is invalid at the head."""
        with self.engine.begin() as conn:
            v = self._view(conn)
            if "ordinary" not in v["may_publish"]:
                raise Refused(f"Constitution publishing paused: {v['classification']}")
            encoded = encoding.make_event(self.seed, self.origin, "ordinary", v["heads"][0],
                                          payload, int(time.time()))
            return self._publish(conn, encoded)

    def publish_join(self) -> bytes:
        """Publish the join the view built while a split awaits one."""
        with self.engine.begin() as conn:
            v = self._view(conn)
            if v["join"] is None:
                raise Refused(f"no split awaits a join: {v['classification']}")
            return self._publish(conn, v["join"])

    def publish_reconciliation(self, tips, adopted, rejected, anchor=None) -> bytes:
        """Draft, sign, check, and publish a reconciliation, then follow it.

        tips:     the heads being resolved (IDs); a tip may lie on another's path
                  for a classic reconciliation.
        adopted:  covered event IDs in replay order; rejected: the rest.
        anchor:   for an anchored reconciliation, the tip it builds on.
        The device finds the base and gathers the covered events as copies.
        Publishing records the owner's choice, so the device follows it even when
        it rejects an event the device followed.
        """
        with self.engine.begin() as conn:
            v = self._view(conn)
            if "reconciliation" not in v["may_publish"]:
                raise Refused(f"may not publish a reconciliation: {v['classification']}")
            lookup = history.build_lookup(self._objects(conn), self.genesis_bytes)
            base, covered = _base_and_covered(lookup, sorted(tips), anchor)
            copies = [self._get(conn, eid) for eid in sorted(covered)]
            payload = encoding.make_reconciliation_payload(tips, adopted, rejected, copies)
            encoded = encoding.make_event(self.seed, self.origin, "reconciliation", base,
                                          payload, int(time.time()))
            return self._publish(conn, encoded)

    # ---- retained copies and export ----

    def get(self, object_id: str):
        """Encoded bytes for an ID, from a standalone object or a retained copy."""
        with self.engine.connect() as conn:
            return self._get(conn, object_id)

    def forget_standalone(self, object_id: str):
        """Delete a standalone object whose exact envelope a reconciliation retains."""
        with self.engine.begin() as conn:
            if conn.execute(text("SELECT 1 FROM spine_retained_copy WHERE object_id = :id"),
                            {"id": object_id}).first() is None:
                raise Refused("no reconciliation retains the same envelope")
            conn.execute(text("DELETE FROM spine_object WHERE object_id = :id"),
                         {"id": object_id})

    def export_bundle(self, tip=None) -> bytes:
        """Export tip (default: the single head) and its verification evidence.

        Follow parents, join tips, and reconciliation copies. Copies stay inside
        their reconciliations rather than becoming separate bundle entries.
        """
        with self.engine.connect() as conn:
            if tip is None:
                heads = self._view(conn)["heads"]
                if len(heads) != 1:
                    raise Refused("several heads; name the tip to export")
                tip = heads[0]
            lookup = history.build_lookup(self._objects(conn), self.genesis_bytes)
            required, carried, pending = {}, set(), [tip]
            while pending:
                object_id = pending.pop()
                if object_id in required:
                    continue
                obj = lookup.objects.get(object_id)
                if obj is None:
                    raise Refused("cannot export: missing ancestor")
                required[object_id] = self._get(conn, object_id)
                body = obj["body"]
                pending.extend(body.get("parents", []))
                if body["kind"] == "join":
                    pending.extend(body["payload"]["tips"])
                for copy in obj.get("copies", []):
                    carried.add(copy["id"])
                    pending.append(copy["id"])
            objects = [encoded for oid, encoded in required.items() if oid not in carried]
        return encoding.make_bundle(self.origin, tip, objects)


def _base_and_covered(lookup, tips, anchor):
    """A reconciliation's parent and the events it must carry (README section 4)."""
    if len(set(tips)) < 2 or len(set(tips)) != len(tips):
        raise Refused("a reconciliation needs at least two distinct tips")
    if anchor is not None and anchor not in tips:
        raise Refused("anchor is not a tip")
    chains = []
    for tip in tips:
        failure, chain = history._chain(lookup, tip)
        if failure:
            raise Refused(f"cannot draft reconciliation: {failure['reason']}")
        chains.append([obj["id"] for obj in chain])
    depth = 0
    while all(len(ch) > depth and ch[depth] == chains[0][depth] for ch in chains):
        depth += 1
    included = set()
    if anchor is not None:
        included, _ = reconcile.history_events(lookup, chains[tips.index(anchor)][depth:])
    covered = {eid for ch in chains for eid in ch[depth:] if eid not in included}
    return (anchor if anchor is not None else chains[0][depth - 1]), covered
