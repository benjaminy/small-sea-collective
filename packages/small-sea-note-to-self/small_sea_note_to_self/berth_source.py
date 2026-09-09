"""How a berth's source-use question is represented on one device.

Two processes read this state and neither may import the other's package: the
Manager owns every decision and every write, and the Hub reads the committed
result to decide whether a provider operation is authorized. So the shape of
the representation lives here, next to the schema that stores it, and the
policy built on it lives in `small_sea_manager.berth_source_decision`.

A candidate is identified by its content rather than its allocation id.
Allocation ids are reused across a delete-and-reinsert, and the same id can
carry different route content on two of a participant's devices, so a key that
did not cover the locator and the account route would let a reviewed candidate
change underneath the person reviewing it.
"""

import hashlib
import json

#: Bumped when the shape of a retained report changes. Retained snapshots are
#: read back after a restart and after the shared rows they describe are gone,
#: so an older one has to be recognizable as older rather than misread.
REPORT_VERSION = 1

#: The route fields that make one candidate distinct from another. `location`
#: is included because the Hub's locator writeback moves it as an execution
#: result: a locator that changed is a different place than the reviewed one.
ROUTE_FIELDS = (
    "allocation_id",
    "cloud_storage_id",
    "protocol",
    "url",
    "location",
    "client_id",
    "path_metadata",
    "created_at",
)


def canonical_bytes(value) -> bytes:
    """The one serialization every digest and key in this feature hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def candidate_key(berth_id: bytes, route: dict) -> str:
    """Identify a candidate by its berth and its whole route content."""
    return hashlib.sha256(
        canonical_bytes(
            {
                "version": REPORT_VERSION,
                "berth_id": berth_id.hex(),
                "route": {field: route[field] for field in ROUTE_FIELDS},
            }
        )
    ).hexdigest()


def report_digest(report: dict) -> bytes:
    """Hash the part of a report a decision is actually made over.

    Generation timestamps and credentials are not in it, so rereading an
    unchanged question recomputes the same digest and a stale choice is
    refused only when the question really moved.
    """
    return hashlib.sha256(
        canonical_bytes(
            {
                "version": report["version"],
                "berth_id": report["berth_id"],
                "candidates": report["candidates"],
            }
        )
    ).digest()


def read_pause(conn, berth_id: bytes):
    """The held pause for one berth, or None. Reads the attached local DB."""
    row = conn.execute(
        "SELECT evidence_digest, evidence_json, detected_at "
        "FROM local.berth_source_pause WHERE berth_id = ?",
        (berth_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "evidence_digest": row[0],
        "report": json.loads(row[1]),
        "detected_at": row[2],
    }


def read_choice(conn, berth_id: bytes):
    """The recorded human decision for one berth, or None."""
    row = conn.execute(
        "SELECT allocation_id, evidence_digest, evidence_json, decided_at "
        "FROM local.berth_write_choice WHERE berth_id = ?",
        (berth_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "allocation_id": row[0].hex(),
        "evidence_digest": row[1],
        "report": json.loads(row[2]),
        "decided_at": row[3],
    }


def saved_route_for_candidate(conn, berth_id: bytes, key: str):
    """The route snapshot a named candidate was reviewed as, or None.

    The Hub's investigation path looks a route up here rather than accepting
    one from its caller. A retained snapshot is evidence, not authority: it
    still has to pass the session, announcement and credential checks, and
    looking it up by key is what keeps an arbitrary caller-supplied route from
    becoming a way around the ordinary path.
    """
    for record in (read_pause(conn, berth_id), read_choice(conn, berth_id)):
        if record is None:
            continue
        for candidate in record["report"].get("candidates", ()):
            if candidate["candidate_key"] == key:
                return {field: candidate[field] for field in ROUTE_FIELDS}
    return None
