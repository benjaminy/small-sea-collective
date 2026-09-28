"""Store verified Team Constitution events and wait for missing parents.

Stored events form a grow-only table. Pending events move there when every parent arrives.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from wrasse_trust.events import (
    ConstitutionEvent,
    decode_event,
    encode_event,
    make_event,
    verify_event,
)


class EventConflictError(Exception):
    def __init__(self, event_id: bytes):
        self.event_id = event_id
        super().__init__(f"event id {event_id.hex()} has different encoded bytes")


def _parents_stored(conn: Connection, event: ConstitutionEvent) -> bool:
    return all(
        conn.execute(
            text("SELECT 1 FROM constitution_event WHERE event_id = :event_id"),
            {"event_id": parent},
        ).first()
        is not None
        for parent in event.parents
    )


def _promote_pending(conn: Connection) -> list[ConstitutionEvent]:
    promoted_events = []
    while True:
        rows = conn.execute(
            text(
                "SELECT event_id, encoded FROM constitution_event_pending "
                "ORDER BY event_id"
            )
        ).all()
        promoted = False
        for event_id, encoded in rows:
            event = decode_event(encoded)
            if not _parents_stored(conn, event):
                continue
            conn.execute(
                text(
                    "INSERT INTO constitution_event (event_id, event_type, encoded) "
                    "VALUES (:event_id, :event_type, :encoded)"
                ),
                {"event_id": event_id, "event_type": event.event_type, "encoded": encoded},
            )
            conn.execute(
                text("DELETE FROM constitution_event_pending WHERE event_id = :event_id"),
                {"event_id": event_id},
            )
            promoted = True
            promoted_events.append(event)
        if not promoted:
            return promoted_events


def add_event(
    conn: Connection, event: ConstitutionEvent
) -> tuple[str, list[ConstitutionEvent]]:
    verify_event(event)
    encoded = encode_event(event)
    existing = []
    for table in ("constitution_event", "constitution_event_pending"):
        row = conn.execute(
            text(f"SELECT encoded FROM {table} WHERE event_id = :event_id"),
            {"event_id": event.event_id},
        ).first()
        if row is not None:
            existing.append(row[0])
    if any(existing_encoded != encoded for existing_encoded in existing):
        raise EventConflictError(event.event_id)
    if existing:
        return "already_present", []

    if not _parents_stored(conn, event):
        conn.execute(
            text(
                "INSERT INTO constitution_event_pending (event_id, encoded) "
                "VALUES (:event_id, :encoded)"
            ),
            {"event_id": event.event_id, "encoded": encoded},
        )
        return "pending", []

    conn.execute(
        text(
            "INSERT INTO constitution_event (event_id, event_type, encoded) "
            "VALUES (:event_id, :event_type, :encoded)"
        ),
        {"event_id": event.event_id, "event_type": event.event_type, "encoded": encoded},
    )
    return "stored", [event, *_promote_pending(conn)]


def current_heads(conn: Connection) -> list[bytes]:
    events = list_events(conn)
    parents = {parent for event in events for parent in event.parents}
    return sorted(event.event_id for event in events if event.event_id not in parents)


def append_local_event(
    conn: Connection,
    event_type: str,
    payload: dict,
    signer_private_key: bytes,
) -> list[ConstitutionEvent]:
    """Sign a new event on top of the current heads and store it; return the newly stored events."""
    event = make_event(event_type, payload, tuple(current_heads(conn)), signer_private_key)
    _status, newly_stored = add_event(conn, event)
    return newly_stored


def get_event(conn: Connection, event_id: bytes) -> ConstitutionEvent | None:
    row = conn.execute(
        text("SELECT encoded FROM constitution_event WHERE event_id = :event_id"),
        {"event_id": event_id},
    ).first()
    return decode_event(row[0]) if row is not None else None


def list_events(conn: Connection) -> list[ConstitutionEvent]:
    rows = conn.execute(
        text("SELECT encoded FROM constitution_event ORDER BY event_id")
    ).all()
    return [decode_event(row[0]) for row in rows]


def list_pending_ids(conn: Connection) -> list[bytes]:
    rows = conn.execute(
        text("SELECT event_id FROM constitution_event_pending ORDER BY event_id")
    ).all()
    return [row[0] for row in rows]
