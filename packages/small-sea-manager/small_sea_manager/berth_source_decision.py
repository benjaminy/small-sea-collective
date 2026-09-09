"""One berth's source-use question: detecting it, holding it, and deciding it.

A participant's devices each publish their own NoteToSelf `core.db`, so two of
them can rotate one berth's cloud allocation while disconnected. Merging those
histories used to be refused by a unique index, which named a commit rather
than a disagreement and rejected every unrelated row travelling with it. The
rows are now allowed to coexist, and the invariant they used to carry moves
here: **more than one live allocation for a berth is an unresolved question,
and this device pauses that berth's normal operations until a human decides.**

Three properties shape the code.

The pause cannot be derived from current state. Evidence that removes the
apparent disagreement -- a sibling adopting this device's row, or deleting its
own -- must not release the pause, because nothing in that evidence is a
decision. So a held pause is a durable device-local row, and only
`resolve_berth_source` deletes it.

Retained evidence outlives the rows it describes. Resolution deletes the losing
allocations from shared state, and a paused sibling that adopts that deletion
must still be able to read what it is deciding about and inspect the location
it may want to keep. So every candidate ever projected stays in
`evidence_json`, marked live or withdrawn, with the complete route content
deliberate inspection needs.

A candidate is identified by its content, not its row id. Allocation ids are
reused across a delete-and-reinsert, and the same id can name different route
content on two devices, so the candidate key hashes the whole route snapshot.

What this module cannot say: which sibling device wrote which row.
`berth_cloud_allocation` has no author column and Cod Sync proves a history's
structure, not its author (#190). The report names retained heads that contain
a row and the row's own `created_at`, and claims no origin beyond that.
"""

import json
from datetime import datetime, timezone

from small_sea_note_to_self.berth_source import (
    REPORT_VERSION,
    canonical_bytes as _canonical,
    candidate_key,
    read_choice,
    read_pause,
    report_digest,
    saved_route_for_candidate,
)
from small_sea_note_to_self.db import attached_note_to_self_connection

__all__ = [
    "BerthSourceError",
    "CandidateNotRestorableError",
    "EvidenceChangedError",
    "REPORT_VERSION",
    "UnknownCandidateError",
    "build_report",
    "candidate_key",
    "live_candidates",
    "project_all_pauses",
    "project_pause",
    "read_choice",
    "read_pause",
    "record_observation",
    "refresh_report",
    "report_digest",
    "resolve_berth_source",
    "saved_route_for_candidate",
]


class BerthSourceError(Exception):
    """Base class for every refusal of a source-use resolution."""


class EvidenceChangedError(BerthSourceError):
    """The reviewed evidence is not what resolution found under its lock.

    Carries the freshly reconstructed report so the caller can show what the
    question became. The pause is untouched.
    """

    def __init__(self, report):
        super().__init__(
            "the evidence changed after it was reviewed; nothing was decided"
        )
        self.report = report


class UnknownCandidateError(BerthSourceError):
    """No reviewed snapshot for this berth carries that candidate key."""


class CandidateNotRestorableError(BerthSourceError):
    """A withdrawn candidate cannot be reinstated from retained evidence alone.

    Restoring a row is an explicit human management decision, not an account
    or provider creation: a missing account, or an allocation id already live
    with different content, leaves the pause held instead.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _route_snapshot(row) -> dict:
    """One candidate's complete route content, as stored."""
    return {
        "allocation_id": row["id"].hex(),
        "cloud_storage_id": row["cloud_storage_id"].hex(),
        "protocol": row["protocol"],
        "url": row["url"],
        "location": row["location"],
        "client_id": row["client_id"],
        "path_metadata": row["path_metadata"],
        "created_at": row["created_at"],
    }


def live_candidates(conn, berth_id: bytes) -> list:
    """Every live allocation for one berth, as route snapshots."""
    rows = conn.execute(
        """
        SELECT
            bca.id,
            bca.cloud_storage_id,
            bca.location,
            bca.created_at,
            cs.protocol,
            cs.url,
            cs.client_id,
            cs.path_metadata
        FROM berth_cloud_allocation bca
        JOIN cloud_storage cs ON cs.id = bca.cloud_storage_id
        WHERE bca.berth_id = ?
        ORDER BY bca.id
        """,
        (berth_id,),
    ).fetchall()
    return [_route_snapshot(row) for row in rows]


# ---------------------------------------------------------------------------
# Retained evidence
# ---------------------------------------------------------------------------


def _retained_candidates(conn, berth_id: bytes) -> dict:
    """Candidates carried forward from a held pause or a recorded decision.

    Both are read: a device that resolved once and then met a fresh
    disagreement should not lose the routes it inspected the first time.
    """
    retained = {}
    for record in (read_choice(conn, berth_id), read_pause(conn, berth_id)):
        if record is None:
            continue
        for candidate in record["report"].get("candidates", ()):
            retained[candidate["candidate_key"]] = candidate
    return retained


def _merge_provenance(existing, additional) -> list:
    seen = {_canonical(entry): entry for entry in existing}
    for entry in additional:
        seen.setdefault(_canonical(entry), entry)
    return [seen[key] for key in sorted(seen)]


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def build_report(
    conn, berth_id: bytes, *, retained_sources=(), retained_observations=()
) -> dict:
    """Reconstruct the source-use report for one berth.

    `retained_sources` is an iterable of `(ref_name, head_sha, routes)` read
    from stored NoteToSelf heads, so a candidate can be attributed to a head
    that contains it rather than to a device. The caller supplies it because
    reading Git is the Manager's business, not this projection's; omitting it
    yields a report whose candidates carry only local snapshots.

    Nothing here is generated per call except the candidates' live flags: no
    timestamp and no credential enters the report, so rereading an unchanged
    question recomputes the same digest.
    """
    candidates = dict(_retained_candidates(conn, berth_id))
    for key in candidates:
        candidates[key] = dict(candidates[key], live=False)

    for route in live_candidates(conn, berth_id):
        key = candidate_key(berth_id, route)
        previous = candidates.get(key, {})
        candidates[key] = {
            **route,
            "candidate_key": key,
            "live": True,
            "provenance": _merge_provenance(
                previous.get("provenance", ()), [{"kind": "local_snapshot"}]
            ),
            "observations": list(previous.get("observations", ())),
        }

    for ref_name, head_sha, routes in retained_sources:
        for route in routes:
            key = candidate_key(berth_id, route)
            previous = candidates.get(key, {})
            candidates[key] = {
                **route,
                "candidate_key": key,
                "live": previous.get("live", False),
                "provenance": _merge_provenance(
                    previous.get("provenance", ()),
                    [
                        {
                            "kind": "retained_head",
                            "ref_name": ref_name,
                            "head_sha": head_sha,
                        }
                    ],
                ),
                "observations": list(previous.get("observations", ())),
            }

    # Git publication can survive a crash that rolls back the report update.
    # Recover only the head/source association a ref proves, not an invented
    # announcement status or fetch disposition.
    for key, ref_name, head_sha in retained_observations:
        candidate = candidates.get(key)
        if candidate is None:
            continue
        if any(o.get("observed_head") == head_sha for o in candidate["observations"]):
            continue
        candidate["observations"] = _merge_provenance(
            candidate["observations"],
            [{"reached": True, "observed_head": head_sha, "ref_name": ref_name}],
        )

    ordered = [candidates[key] for key in sorted(candidates)]
    unreachable = sorted(
        candidate["candidate_key"]
        for candidate in ordered
        if any(not obs.get("reached", True) for obs in candidate["observations"])
    )
    return {
        "version": REPORT_VERSION,
        "berth_id": berth_id.hex(),
        "candidates": ordered,
        "unavailable": {
            # Never establishable from this data, and worth saying out loud so
            # a human does not read `created_at` order as authorship.
            "authorship": "no record connects an allocation row to a device",
            "unreachable_candidates": unreachable,
        },
    }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def project_pause(
    conn, berth_id: bytes, *, retained_sources=(), retained_observations=(), now=None
) -> dict:
    """Record what this device now knows about one berth's placement.

    Runs on the caller's connection inside the caller's transaction, so the
    pause and the shared rows that opened it commit together: a crash before
    the commit exposes neither, and a crash after it leaves the pause durable
    whether or not the Git recording finished. No post-commit callback could
    make that promise.

    Called both before and after a mutation. The call before is what preserves
    a row a deletion is about to remove while a pause is already held; the call
    after is the detection itself. Refreshing a held pause never clears it --
    only `resolve_berth_source` does -- so a sibling that withdrew its own row
    improves the explanation without deciding anything.
    """
    report = build_report(
        conn, berth_id, retained_sources=retained_sources,
        retained_observations=retained_observations,
    )
    digest = report_digest(report)
    contested = sum(1 for candidate in report["candidates"] if candidate["live"]) > 1
    existing = conn.execute(
        "SELECT detected_at FROM local.berth_source_pause WHERE berth_id = ?",
        (berth_id,),
    ).fetchone()
    if not contested and existing is None:
        return report
    conn.execute(
        """
        INSERT INTO local.berth_source_pause (
            berth_id, evidence_digest, evidence_json, detected_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(berth_id) DO UPDATE SET
            evidence_digest = excluded.evidence_digest,
            evidence_json = excluded.evidence_json
        """,
        (
            berth_id,
            digest,
            json.dumps(report),
            # A refresh keeps the moment the question opened; only a first
            # detection stamps one.
            existing["detected_at"] if existing else (now or _now_iso()),
        ),
    )
    return report


def project_all_pauses(conn, *, now=None) -> None:
    """Project every berth this device holds allocations or a pause for.

    Adoption applies a whole row delta, so the berths it touched are not known
    in advance. The set is small -- one row per berth this participant placed
    -- and scanning it costs less than teaching `splice_merge` about berths.
    """
    berth_ids = {
        row[0]
        for row in conn.execute("SELECT DISTINCT berth_id FROM berth_cloud_allocation")
    }
    berth_ids |= {
        row[0]
        for row in conn.execute("SELECT berth_id FROM local.berth_source_pause")
    }
    for berth_id in sorted(berth_ids):
        project_pause(conn, berth_id, now=now)


def refresh_report(
    root_dir, participant_hex, berth_id: bytes, *, retained_sources_fn=None,
    retained_observations_fn=None,
) -> dict:
    """Re-project one berth and return what a human would decide over.

    Not read-only: reprojecting is how a candidate a sibling has since deleted
    stays in the retained evidence, and how a newly arrived competing row
    becomes a held pause on a device that has not adopted anything since.
    """
    conn = attached_note_to_self_connection(root_dir, participant_hex)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            retained_sources = (
                retained_sources_fn() if retained_sources_fn is not None else ()
            )
            report = project_pause(
                conn, berth_id, retained_sources=retained_sources,
                retained_observations=(
                    retained_observations_fn() if retained_observations_fn else ()
                ),
            )
            pause = read_pause(conn, berth_id)
            choice = read_choice(conn, berth_id)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return {
        "report": report,
        "evidence_digest": report_digest(report),
        "pause": pause,
        "choice": choice,
    }


# ---------------------------------------------------------------------------
# Investigation outcomes
# ---------------------------------------------------------------------------


def record_observation(
    root_dir, participant_hex, berth_id: bytes, key: str, observation: dict,
    *, publish_observation_fn=None,
) -> bool:
    """Attach one investigation outcome to a candidate's retained evidence.

    The outcome is associated with the candidate that was inspected, not with
    a berth or a head, so two locations that return the same head stay two
    observations. Recording an outcome is not integration and not a decision:
    it changes the explanation and therefore the digest, which is exactly why
    a choice reviewed before it is refused as stale.

    The Manager may publish verified refs in `publish_observation_fn`, under
    the same writer reservation as resolution. Fetching happens before this
    call. A crash after ref publication is repaired by report reconstruction.

    Returns whether the outcome was attached to a held pause. If resolution
    finished first, refs still preserve the later observation without changing
    the immutable evidence of the earlier decision.
    """
    conn = attached_note_to_self_connection(root_dir, participant_hex)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if publish_observation_fn is not None:
                observation.update(publish_observation_fn())
            pause = read_pause(conn, berth_id)
            if pause is None:
                # Nothing to attach it to. A resolved berth keeps the evidence
                # its decision was made over, and a later look at a retained
                # location does not get to rewrite that record.
                conn.execute("COMMIT")
                return False
            report = pause["report"]
            for candidate in report["candidates"]:
                if candidate["candidate_key"] != key:
                    continue
                candidate["observations"] = _merge_provenance(
                    candidate["observations"], [observation]
                )
                break
            else:
                raise UnknownCandidateError(
                    f"no reviewed candidate {key} for berth {berth_id.hex()}"
                )
            conn.execute(
                "UPDATE local.berth_source_pause "
                "SET evidence_digest = ?, evidence_json = ? WHERE berth_id = ?",
                (report_digest(report), json.dumps(report), berth_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return True


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_berth_source(
    root_dir,
    participant_hex,
    berth_id: bytes,
    key: str,
    reviewed_digest: bytes,
    *,
    retained_sources_fn=None,
    retained_observations_fn=None,
    now=None,
) -> dict:
    """Apply one human decision about which location this berth keeps.

    Comparison and mutation run under a single `BEGIN IMMEDIATE` reservation,
    so a new candidate, a locator writeback or a freshly published observation
    either enters the comparison and invalidates the reviewed digest, or lands
    after the choice commits and is seen as the next question. Provider I/O
    and verification stay outside it; only the short publication of an
    observation ref shares it.

    Returns the report the decision was made over. Raises `EvidenceChangedError`
    with the fresh report when the question moved under the reviewer.
    """
    conn = attached_note_to_self_connection(root_dir, participant_hex)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            retained_sources = (
                retained_sources_fn() if retained_sources_fn is not None else ()
            )
            report = build_report(
                conn, berth_id, retained_sources=retained_sources,
                retained_observations=(
                    retained_observations_fn() if retained_observations_fn else ()
                ),
            )
            if report_digest(report) != reviewed_digest:
                raise EvidenceChangedError(report)

            chosen = next(
                (c for c in report["candidates"] if c["candidate_key"] == key), None
            )
            if chosen is None:
                raise UnknownCandidateError(
                    f"no reviewed candidate {key} for berth {berth_id.hex()}"
                )

            allocation_id = bytes.fromhex(chosen["allocation_id"])
            if not chosen["live"]:
                _restore_withdrawn_candidate(conn, berth_id, chosen)
            conn.execute(
                "DELETE FROM berth_cloud_allocation "
                "WHERE berth_id = ? AND id != ?",
                (berth_id, allocation_id),
            )
            conn.execute(
                """
                INSERT INTO local.berth_write_choice (
                    berth_id, allocation_id, evidence_digest, evidence_json, decided_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(berth_id) DO UPDATE SET
                    allocation_id = excluded.allocation_id,
                    evidence_digest = excluded.evidence_digest,
                    evidence_json = excluded.evidence_json,
                    decided_at = excluded.decided_at
                """,
                (
                    berth_id,
                    allocation_id,
                    reviewed_digest,
                    json.dumps(report),
                    now or _now_iso(),
                ),
            )
            conn.execute(
                "DELETE FROM local.berth_source_pause WHERE berth_id = ?", (berth_id,)
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return report


def _restore_withdrawn_candidate(conn, berth_id: bytes, chosen: dict) -> None:
    """Reinstate a retained allocation row, or refuse and keep the pause.

    Deliberately narrow. Restoration writes back exactly the reviewed row and
    requires the account it names to still exist with the same route fields:
    an account that was disconnected, or one whose URL moved, is a different
    place than the human reviewed. It creates no account, no credential and no
    provider object.
    """
    allocation_id = bytes.fromhex(chosen["allocation_id"])
    cloud_storage_id = bytes.fromhex(chosen["cloud_storage_id"])
    account = conn.execute(
        "SELECT protocol, url, client_id, path_metadata FROM cloud_storage WHERE id = ?",
        (cloud_storage_id,),
    ).fetchone()
    if account is None:
        raise CandidateNotRestorableError(
            f"the account {chosen['cloud_storage_id']} this candidate names is no "
            "longer registered on this device"
        )
    mismatched = [
        field
        for field in ("protocol", "url", "client_id", "path_metadata")
        if account[field] != chosen[field]
    ]
    if mismatched:
        raise CandidateNotRestorableError(
            f"the account {chosen['cloud_storage_id']} no longer matches the "
            f"reviewed route in {sorted(mismatched)}"
        )
    # The chosen snapshot is withdrawn, so any live row under its id is
    # different content wearing a reused identifier.
    if conn.execute(
        "SELECT 1 FROM berth_cloud_allocation WHERE id = ?", (allocation_id,)
    ).fetchone() is not None:
        raise CandidateNotRestorableError(
            f"allocation {chosen['allocation_id']} is live with different "
            "content than the reviewed candidate"
        )
    conn.execute(
        """
        INSERT INTO berth_cloud_allocation (
            id, berth_id, cloud_storage_id, location, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            allocation_id,
            berth_id,
            cloud_storage_id,
            chosen["location"],
            chosen["created_at"],
        ),
    )
