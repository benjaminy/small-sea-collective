"""Micro tests for admission-as-Constitution-records (issue #164).

These exercise the four append-only record types (proposal / acceptance /
endorsement / finalization) directly: signatures verify through
`wrasse_trust.constitution` against independently recomputed canonical bytes,
the self-certifying acceptance rejects a substituted key, the separable
label payload really is separable, the steward/contributor preset expands
per-berth, one steward's two devices count once, and refusals never mutate a
row. The tamper tests at the bottom cover the 2026-07-04 committee findings:
the signed mode_plan cannot be escalated or dropped, and forged
endorsement/acceptance/snapshot rows are refused at decision points.

Runs entirely over LocalFolderRemote -- no MinIO or Hub.
"""

import base64
import json
import pathlib
import shutil
import sqlite3

import cod_sync.protocol as CS
import pytest

import small_sea_manager.provisioning as provisioning
from wrasse_trust.constitution import (
    canonical_constitution_bytes,
    derive_record_id,
    sign_constitution_record,
    verify_constitution_record,
)
from wrasse_trust.keys import ProtectionLevel, generate_key_pair, key_id_from_public


def _push(repo_dir: pathlib.Path, cloud_dir: pathlib.Path):
    cod = CS.CodSync("origin", repo_dir=repo_dir)
    cod.remote = CS.LocalFolderRemote(str(cloud_dir))
    cod.push_to_remote(["main"])


def _setup_team(root: pathlib.Path, *, quorum: int | None = None):
    cloud = root / "alice-cloud"
    cloud.mkdir()
    alice_hex = provisioning.create_new_participant(root, "Alice")
    bob_hex = provisioning.create_new_participant(root, "Bob")
    provisioning.add_cloud_storage(root, alice_hex, protocol="localfolder", url=str(cloud))
    provisioning.create_team(root, alice_hex, "ProjectX")
    if quorum is not None:
        provisioning.set_team_admission_policy(root, alice_hex, "ProjectX", quorum=quorum)
    alice_sync = root / "Participants" / alice_hex / "ProjectX" / "Sync"
    _push(alice_sync, cloud)
    return alice_hex, bob_hex, cloud, alice_sync


def _admit(root, alice_hex, invitee_hex, cloud, *, mode_plan=None, label="Bob"):
    """Run a quorum-1 admission end to end; returns the acceptance token dict."""
    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label=label,
        mode_plan=mode_plan,
    )
    _push(root / "Participants" / alice_hex / "ProjectX" / "Sync", cloud)
    acceptance = provisioning.accept_invitation(
        root, invitee_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    return json.loads(base64.b64decode(acceptance).decode())


def _device_public_key(conn, device_key_id: bytes) -> bytes:
    return conn.execute(
        "SELECT public_key FROM team_device WHERE device_key_id = ?", (device_key_id,)
    ).fetchone()[0]


def _hexn(value) -> str | None:
    return value.hex() if value is not None else None


# --------------------------------------------------------------------------- #
# 1. Every finalized-admission record verifies against recomputed canonical bytes
# --------------------------------------------------------------------------- #


def test_finalized_admission_writes_four_verifiable_records(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    _admit(root, alice_hex, bob_hex, cloud)

    db = alice_sync / "core.db"
    with sqlite3.connect(str(db)) as conn:
        proposal = conn.execute(
            "SELECT record_id, record_type, author_teammate_id, author_device_key_id, "
            "created_at, anchor_commit, constitution_digest, nonce, team_id, "
            "invitee_teammate_id, invitee_label_commitment, expires_at, signature, mode_plan "
            "FROM admission_proposal"
        ).fetchone()
        acceptance = conn.execute(
            "SELECT record_id, record_type, author_teammate_id, author_device_key_id, "
            "created_at, subject_record_id, nonce, invitee_device_public_key, "
            "invitee_bootstrap_key, signature FROM admission_acceptance"
        ).fetchone()
        endorsement = conn.execute(
            "SELECT record_type, author_teammate_id, author_device_key_id, created_at, "
            "anchor_commit, constitution_digest, subject_record_id, subject_digest, signature "
            "FROM endorsement"
        ).fetchone()
        finalization = conn.execute(
            "SELECT record_type, author_teammate_id, author_device_key_id, created_at, "
            "anchor_commit, constitution_digest, subject_record_id, subject_digest, "
            "endorsement_count, signature FROM finalization"
        ).fetchone()

        # -- proposal: signed by the inviter's device, no embedded invitee key --
        p_signed = {
            "record_type": proposal[1],
            "author_teammate_id": proposal[2].hex(),
            "author_device_key_id": proposal[3].hex(),
            "created_at": proposal[4],
            "anchor_commit": proposal[5],
            "constitution_digest": proposal[6].hex(),
            "schema_version": 1,
            "nonce": proposal[7].hex(),
            "team_id": proposal[8].hex(),
            "invitee_teammate_id": proposal[9].hex(),
            "invitee_label_commitment": _hexn(proposal[10]),
            "expires_at": proposal[11],
            "mode_plan": json.loads(proposal[13]),
        }
        assert verify_constitution_record(
            _device_public_key(conn, proposal[3]),
            canonical_constitution_bytes(p_signed),
            proposal[12],
        )

        # -- acceptance: self-certified by its own embedded device key --
        a_signed = {
            "record_type": acceptance[1],
            "author_teammate_id": acceptance[2].hex(),
            "author_device_key_id": acceptance[3].hex(),
            "created_at": acceptance[4],
            "anchor_commit": None,
            "constitution_digest": None,
            "schema_version": 1,
            "subject_record_id": acceptance[5].hex(),
            "nonce": acceptance[6].hex(),
            "invitee_device_public_key": acceptance[7].hex(),
            "invitee_bootstrap_key": acceptance[8].hex(),
        }
        assert verify_constitution_record(
            acceptance[7], canonical_constitution_bytes(a_signed), acceptance[9]
        )

        # -- endorsement + finalization: signed by the inviter's device --
        for rec in (endorsement, finalization):
            signed = {
                "record_type": rec[0],
                "author_teammate_id": rec[1].hex(),
                "author_device_key_id": rec[2].hex(),
                "created_at": rec[3],
                "anchor_commit": rec[4],
                "constitution_digest": rec[5].hex(),
                "schema_version": 1,
                "subject_record_id": rec[6].hex(),
                "subject_digest": rec[7].hex(),
            }
            if rec is finalization:
                signed["endorsement_count"] = rec[8]
            signature = rec[-1]
            assert verify_constitution_record(
                _device_public_key(conn, rec[2]),
                canonical_constitution_bytes(signed),
                signature,
            )

        # subject_digest is the untruncated commitment over the acceptance's bytes
        assert endorsement[7] == finalization[7]
        assert endorsement[7][:16] == acceptance[0]  # record_id = sha256(canonical)[:16]


# --------------------------------------------------------------------------- #
# 2. Separable columns are excluded from the signed bytes
# --------------------------------------------------------------------------- #


def test_separable_label_payload_does_not_affect_proposal_signature(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
        mode_plan=provisioning.mode_plan_for_preset("contributor"),
    )

    db = alice_sync / "core.db"
    with sqlite3.connect(str(db)) as conn:
        # Drop the separable label payload entirely, as a syncing peer may.
        # mode_plan is deliberately NOT separable: it is signed (see the
        # tamper tests below).
        conn.execute("UPDATE admission_proposal SET invitee_label_payload = NULL")
        conn.commit()
        row = conn.execute(
            "SELECT author_device_key_id, created_at, anchor_commit, constitution_digest, "
            "nonce, team_id, invitee_teammate_id, invitee_label_commitment, expires_at, "
            "author_teammate_id, signature, mode_plan FROM admission_proposal"
        ).fetchone()
        signed = {
            "record_type": "admission_proposal",
            "author_teammate_id": row[9].hex(),
            "author_device_key_id": row[0].hex(),
            "created_at": row[1],
            "anchor_commit": row[2],
            "constitution_digest": row[3].hex(),
            "schema_version": 1,
            "nonce": row[4].hex(),
            "team_id": row[5].hex(),
            "invitee_teammate_id": row[6].hex(),
            "invitee_label_commitment": _hexn(row[7]),
            "expires_at": row[8],
            "mode_plan": json.loads(row[11]),
        }
        assert verify_constitution_record(
            _device_public_key(conn, row[0]), canonical_constitution_bytes(signed), row[10]
        )


# --------------------------------------------------------------------------- #
# 3. Self-certification boundary
# --------------------------------------------------------------------------- #


def _make_proposal_and_accept(root, alice_hex, bob_hex, cloud):
    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(root / "Participants" / alice_hex / "ProjectX" / "Sync", cloud)
    acceptance_b64 = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    return json.loads(base64.b64decode(acceptance_b64).decode())


def _retokenize(acceptance_dict) -> str:
    return base64.b64encode(
        json.dumps(acceptance_dict, sort_keys=True, separators=(",", ":")).encode()
    ).decode()


def test_acceptance_signed_by_other_key_is_rejected(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    acceptance = _make_proposal_and_accept(root, alice_hex, bob_hex, cloud)

    # Swap in a different embedded device key. The recorded signature no longer
    # matches, and author_device_key_id no longer matches the embedded key.
    other_key, _ = generate_key_pair(ProtectionLevel.DAILY)
    acceptance["invitee_device_public_key"] = other_key.public_key.hex()

    with pytest.raises(ValueError, match="signature is invalid|does not match its embedded"):
        provisioning.complete_invitation_acceptance(
            root, alice_hex, "ProjectX", _retokenize(acceptance)
        )


def test_acceptance_with_wrong_nonce_is_rejected(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    acceptance = _make_proposal_and_accept(root, alice_hex, bob_hex, cloud)

    # Flip the nonce: canonical bytes change, so the self-certifying record_id
    # check fails before anything touches the proposal.
    acceptance["nonce"] = ("f" * len(acceptance["nonce"]))

    with pytest.raises(ValueError, match="record_id does not match|nonce does not match"):
        provisioning.complete_invitation_acceptance(
            root, alice_hex, "ProjectX", _retokenize(acceptance)
        )


# --------------------------------------------------------------------------- #
# 4. Preset expansion per architecture.md (contributor: proposal-only on Core,
#    automatic elsewhere) -- fails against a uniform-role implementation.
# --------------------------------------------------------------------------- #


def test_contributor_preset_expands_per_berth(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    db = alice_sync / "core.db"

    # Add a second, non-Core berth to the team before admission.
    other_app_id = provisioning.uuid7()
    other_berth_id = provisioning.uuid7()
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT INTO app (id, name) VALUES (?, ?)", (other_app_id, "SscFiles"))
        conn.execute(
            "INSERT INTO team_app_berth (id, app_id) VALUES (?, ?)",
            (other_berth_id, other_app_id),
        )
        conn.commit()
    repo = provisioning._Repo(alice_sync / ".git", alice_sync)
    repo.stage(["core.db"])
    repo.commit("Add non-Core berth")
    _push(alice_sync, cloud)

    acceptance = _admit(
        root, alice_hex, bob_hex, cloud,
        mode_plan=provisioning.mode_plan_for_preset("contributor"),
    )
    bob_teammate_id = bytes.fromhex(acceptance["author_teammate_id"])

    with sqlite3.connect(str(db)) as conn:
        core_berth_id = conn.execute(
            "SELECT tab.id FROM team_app_berth tab JOIN app a ON a.id = tab.app_id "
            "WHERE a.name = 'SmallSeaCollectiveCore'"
        ).fetchone()[0]

        modes = dict(
            conn.execute(
                "SELECT berth_id, mode FROM integration_mode_change WHERE teammate_id = ?",
                (bob_teammate_id,),
            ).fetchall()
        )
        assert modes[core_berth_id] == "proposal-only"
        assert modes[other_berth_id] == "automatic"

        roles = dict(
            conn.execute(
                "SELECT berth_id, role FROM berth_role WHERE teammate_id = ?",
                (bob_teammate_id,),
            ).fetchall()
        )
        assert roles[core_berth_id] == "read-only"
        assert roles[other_berth_id] == "read-write"

        # Each expansion record independently verifies.
        for berth_id, signature, author_device_key_id, created_at, anchor_commit, digest, mode in conn.execute(
            "SELECT berth_id, signature, author_device_key_id, created_at, anchor_commit, "
            "constitution_digest, mode FROM integration_mode_change WHERE teammate_id = ?",
            (bob_teammate_id,),
        ).fetchall():
            signed = {
                "record_type": "integration_mode_change",
                "author_teammate_id": provisioning._team_row(root, alice_hex, "ProjectX")[1].hex(),
                "author_device_key_id": author_device_key_id.hex(),
                "created_at": created_at,
                "anchor_commit": anchor_commit,
                "constitution_digest": digest.hex(),
                "schema_version": 1,
                "teammate_id": bob_teammate_id.hex(),
                "berth_id": berth_id.hex(),
                "mode": mode,
            }
            assert verify_constitution_record(
                _device_public_key(conn, author_device_key_id),
                canonical_constitution_bytes(signed),
                signature,
            )


# --------------------------------------------------------------------------- #
# 5. The double-count fix: one steward's two devices endorse once
# --------------------------------------------------------------------------- #


def test_one_steward_two_devices_endorse_once(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root, quorum=2)
    # Quorum 2: acceptance records Alice's single auto-endorsement, then stays
    # awaiting_quorum (no projections).
    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)

    db = alice_sync / "core.db"
    engine = provisioning._sqlite_engine(db)
    alice_teammate_id = provisioning._team_row(root, alice_hex, "ProjectX")[1]
    with engine.begin() as conn:
        proposal_id = conn.execute(
            provisioning.text("SELECT record_id FROM admission_proposal")
        ).fetchone()[0]
        proposal_row = provisioning._load_proposal_row(conn, proposal_id)
        assert provisioning._endorsement_count(conn, proposal_id) == 1

        # Alice endorses again from a *second* device (fresh keypair, same
        # teammate). The UNIQUE(subject_record_id, author_teammate_id) constraint
        # keeps the count at 1 -- the old per-device dedupe would have made it 2.
        second_device, second_private = generate_key_pair(ProtectionLevel.DAILY)
        provisioning._append_endorsement(
            conn,
            proposal_row=proposal_row,
            endorser_teammate_id=alice_teammate_id,
            author_private_key=second_private,
            author_public_key=second_device.public_key,
            anchor_commit=None,
        )
        assert provisioning._endorsement_count(conn, proposal_id) == 1
    engine.dispose()


# --------------------------------------------------------------------------- #
# 6. Refusals don't mutate: an expired proposal rejects endorsement, unchanged
# --------------------------------------------------------------------------- #


def test_expired_proposal_refuses_endorsement_without_mutation(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root, quorum=2)
    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)

    db = alice_sync / "core.db"
    proposal_id_hex = provisioning.list_invitations(root, alice_hex, "ProjectX")[0]["id"]

    # Force the (unsigned-view) expiry into the past; the signed digest column is
    # untouched, so this only flips the computed status to `expired`.
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE admission_proposal SET expires_at = '2000-01-01T00:00:00+00:00'"
        )
        conn.commit()
        before = conn.execute("SELECT COUNT(*) FROM endorsement").fetchone()[0]

    with pytest.raises(ValueError, match="expired"):
        provisioning.endorse_admission(root, alice_hex, "ProjectX", proposal_id_hex)

    with sqlite3.connect(str(db)) as conn:
        after = conn.execute("SELECT COUNT(*) FROM endorsement").fetchone()[0]
        assert after == before  # refusal added no endorsement
        assert provisioning.list_invitations(root, alice_hex, "ProjectX")[0]["status"] == "expired"


# --------------------------------------------------------------------------- #
# 7. Committee finding 1: mode_plan is signed -- it cannot be escalated or
#    dropped between proposal creation and finalization.
# --------------------------------------------------------------------------- #


def test_tampered_mode_plan_blocks_completion(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
        mode_plan=provisioning.mode_plan_for_preset("contributor"),
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )

    # Escalate the stored plan to the steward expansion, as a malicious merge
    # could. The plan is inside the signed canonical bytes, so the record no
    # longer matches its record_id/signature.
    db = alice_sync / "core.db"
    steward_plan = json.dumps(
        provisioning.mode_plan_for_preset("steward"), sort_keys=True, separators=(",", ":")
    )
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE admission_proposal SET mode_plan = ?", (steward_plan,))
        conn.commit()

    with pytest.raises(ValueError, match="record_id does not match|signature is invalid"):
        provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)

    with sqlite3.connect(str(db)) as conn:
        # Refused before any admission side effect: no teammate, no finalization.
        assert conn.execute("SELECT COUNT(*) FROM teammate").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM finalization").fetchone()[0] == 0
        # And the drop attack is gone at the schema level: mode_plan is NOT NULL.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE admission_proposal SET mode_plan = NULL")


# --------------------------------------------------------------------------- #
# 8. Committee finding 2: quorum counts only endorsements that verify
#    end-to-end. A well-formed row forged in a real steward's name (signed by
#    an attacker key) meets the old COUNT(*) quorum but not the validated one.
# --------------------------------------------------------------------------- #


def test_forged_endorsements_do_not_count_toward_quorum(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    carol_hex = provisioning.create_new_participant(root, "Carol")
    _admit(root, alice_hex, carol_hex, cloud, label="Carol")  # second steward
    provisioning.set_team_admission_policy(root, alice_hex, "ProjectX", quorum=2)
    _push(alice_sync, cloud)

    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    proposal_hex = next(p["id"] for p in proposals if p["status"] == "awaiting_quorum")
    proposal_id = bytes.fromhex(proposal_hex)

    # Give Carol her own up-to-date replica before Alice's gets poisoned.
    carol_sync = root / "Participants" / carol_hex / "ProjectX" / "Sync"
    shutil.copy2(alice_sync / "core.db", carol_sync / "core.db")

    # Forge an endorsement in Carol's name: correct author/device ids, correct
    # subject_digest and constitution_digest, correctly derived record_id --
    # but signed by an attacker key instead of Carol's device key.
    db = alice_sync / "core.db"
    with sqlite3.connect(str(db)) as conn:
        carol_teammate_id, carol_device_key_id = conn.execute(
            "SELECT m.id, d.device_key_id FROM teammate m "
            "JOIN team_device d ON d.teammate_id = m.id WHERE m.display_name = 'Carol'"
        ).fetchone()
        proposal_digest, snapshot_json = conn.execute(
            "SELECT constitution_digest, constitution_snapshot_json "
            "FROM admission_proposal WHERE record_id = ?",
            (proposal_id,),
        ).fetchone()
        subject_digest = conn.execute(
            "SELECT subject_digest FROM endorsement WHERE subject_record_id = ?",
            (proposal_id,),
        ).fetchone()[0]
        _attacker_key, attacker_private = generate_key_pair(ProtectionLevel.DAILY)
        now = provisioning._now_iso()
        forged_signed = {
            **provisioning._record_envelope_fields(
                record_type="endorsement",
                author_teammate_id=carol_teammate_id,
                author_device_key_id=carol_device_key_id,
                created_at=now,
                anchor_commit=None,
                constitution_digest=proposal_digest,
            ),
            "subject_record_id": proposal_id.hex(),
            "subject_digest": subject_digest.hex(),
        }
        forged_canonical = canonical_constitution_bytes(forged_signed)
        conn.execute(
            "INSERT INTO endorsement ("
            "record_id, record_type, author_teammate_id, author_device_key_id, created_at, "
            "anchor_commit, constitution_digest, constitution_snapshot_json, schema_version, "
            "subject_record_id, subject_digest, signature) "
            "VALUES (?, 'endorsement', ?, ?, ?, NULL, ?, ?, 1, ?, ?, ?)",
            (
                derive_record_id(forged_canonical),
                carol_teammate_id,
                carol_device_key_id,
                now,
                proposal_digest,
                snapshot_json,
                proposal_id,
                subject_digest,
                sign_constitution_record(attacker_private, forged_canonical),
            ),
        )
        conn.commit()
        # The raw row count now meets quorum: the pre-fix COUNT(*) check would
        # have finalized on the forged row.
        assert conn.execute(
            "SELECT COUNT(*) FROM endorsement WHERE subject_record_id = ?", (proposal_id,)
        ).fetchone()[0] == 2

    with pytest.raises(ValueError, match="quorum"):
        provisioning.finalize_admission(root, alice_hex, "ProjectX", proposal_hex)

    # A genuine endorsement from Carol's real device key does meet quorum.
    provisioning.endorse_admission(root, carol_hex, "ProjectX", proposal_hex)
    shutil.copy2(carol_sync / "core.db", alice_sync / "core.db")
    provisioning.finalize_admission(root, alice_hex, "ProjectX", proposal_hex)
    statuses = {
        p["id"]: p["status"] for p in provisioning.list_invitations(root, alice_hex, "ProjectX")
    }
    assert statuses[proposal_hex] == "finalized"


# --------------------------------------------------------------------------- #
# 9. The unsigned snapshot JSON is bound to the signed digest before any
#    eligibility decision reads it.
# --------------------------------------------------------------------------- #


def test_tampered_snapshot_json_is_rejected_against_signed_digest(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root, quorum=2)
    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposal_hex = provisioning.list_invitations(root, alice_hex, "ProjectX")[0]["id"]

    # Graft an attacker device onto the snapshot's device list. The JSON column
    # is not itself signed; the digest binding must catch the mismatch.
    db = alice_sync / "core.db"
    with sqlite3.connect(str(db)) as conn:
        snapshot = json.loads(
            conn.execute("SELECT constitution_snapshot_json FROM admission_proposal").fetchone()[0]
        )
        attacker_key, _ = generate_key_pair(ProtectionLevel.DAILY)
        teammate_hex = next(iter(snapshot["teammate_devices"]))
        snapshot["teammate_devices"][teammate_hex].append(
            key_id_from_public(attacker_key.public_key).hex()
        )
        conn.execute(
            "UPDATE admission_proposal SET constitution_snapshot_json = ?",
            (json.dumps(snapshot, sort_keys=True),),
        )
        conn.commit()

    with pytest.raises(ValueError, match="snapshot does not match its signed digest"):
        provisioning.endorse_admission(root, alice_hex, "ProjectX", proposal_hex)


# --------------------------------------------------------------------------- #
# 10. A merged acceptance row is re-verified at finalization, not trusted as
#     already-checked local state.
# --------------------------------------------------------------------------- #


def test_tampered_acceptance_row_is_rejected_at_finalization(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root, quorum=2)
    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposal_hex = provisioning.list_invitations(root, alice_hex, "ProjectX")[0]["id"]

    # Swap the embedded self-certification key for an attacker's, as a merged
    # row could carry.
    db = alice_sync / "core.db"
    with sqlite3.connect(str(db)) as conn:
        attacker_key, _ = generate_key_pair(ProtectionLevel.DAILY)
        conn.execute(
            "UPDATE admission_acceptance SET invitee_device_public_key = ?",
            (attacker_key.public_key,),
        )
        conn.commit()

    with pytest.raises(ValueError, match="does not match its embedded device key"):
        provisioning.finalize_admission(root, alice_hex, "ProjectX", proposal_hex)


# --------------------------------------------------------------------------- #
# 11. Committee round 3: forged finalization rows are ignored, not terminal.
#     They must neither report the proposal finalized nor block ("already
#     finalized") the legitimate finalization, which -- finalization having no
#     UNIQUE(subject_record_id) -- is appended alongside the invalid rows.
# --------------------------------------------------------------------------- #


def test_forged_finalization_rows_are_ignored_and_do_not_block(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    carol_hex = provisioning.create_new_participant(root, "Carol")
    _admit(root, alice_hex, carol_hex, cloud, label="Carol")  # second steward
    provisioning.set_team_admission_policy(root, alice_hex, "ProjectX", quorum=2)
    _push(alice_sync, cloud)

    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    proposal_hex = next(p["id"] for p in proposals if p["status"] == "awaiting_quorum")
    proposal_id = bytes.fromhex(proposal_hex)

    # Carol's clean replica, before Alice's gets poisoned.
    carol_sync = root / "Participants" / carol_hex / "ProjectX" / "Sync"
    shutil.copy2(alice_sync / "core.db", carol_sync / "core.db")

    db = alice_sync / "core.db"

    def _insert_forged_finalizations():
        # Variant 1: in the inviter's (Alice's) name, everything consistent
        # except the signature, which is an attacker key's.
        with sqlite3.connect(str(db)) as conn:
            alice_teammate_id, alice_device_key_id = conn.execute(
                "SELECT m.id, d.device_key_id FROM teammate m "
                "JOIN team_device d ON d.teammate_id = m.id WHERE m.display_name = 'Alice'"
            ).fetchone()
            proposal_digest, snapshot_json = conn.execute(
                "SELECT constitution_digest, constitution_snapshot_json "
                "FROM admission_proposal WHERE record_id = ?",
                (proposal_id,),
            ).fetchone()
            subject_digest = conn.execute(
                "SELECT subject_digest FROM endorsement WHERE subject_record_id = ?",
                (proposal_id,),
            ).fetchone()[0]
            _attacker_key, attacker_private = generate_key_pair(ProtectionLevel.DAILY)
            now = provisioning._now_iso()
            forged_signed = {
                **provisioning._record_envelope_fields(
                    record_type="finalization",
                    author_teammate_id=alice_teammate_id,
                    author_device_key_id=alice_device_key_id,
                    created_at=now,
                    anchor_commit=None,
                    constitution_digest=proposal_digest,
                ),
                "subject_record_id": proposal_id.hex(),
                "subject_digest": subject_digest.hex(),
                "endorsement_count": 2,
            }
            forged_canonical = canonical_constitution_bytes(forged_signed)
            conn.execute(
                "INSERT INTO finalization ("
                "record_id, record_type, author_teammate_id, author_device_key_id, created_at, "
                "anchor_commit, constitution_digest, constitution_snapshot_json, schema_version, "
                "subject_record_id, subject_digest, endorsement_count, signature) "
                "VALUES (?, 'finalization', ?, ?, ?, NULL, ?, ?, 1, ?, ?, 2, ?)",
                (
                    derive_record_id(forged_canonical),
                    alice_teammate_id,
                    alice_device_key_id,
                    now,
                    proposal_digest,
                    snapshot_json,
                    proposal_id,
                    subject_digest,
                    sign_constitution_record(attacker_private, forged_canonical),
                ),
            )
            conn.commit()

        # Variant 2: a *validly signed* finalization authored by Carol, who is
        # a steward but not the proposal's author -- the inviter-only rule
        # makes it non-terminal.
        carol_private, carol_public = provisioning.get_current_team_device_key(
            root, carol_hex, "ProjectX"
        )
        engine = provisioning._sqlite_engine(db)
        try:
            with engine.begin() as conn:
                carol_teammate_id = conn.execute(
                    provisioning.text("SELECT id FROM teammate WHERE display_name = 'Carol'")
                ).fetchone()[0]
                provisioning._append_finalization(
                    conn,
                    proposal_row=provisioning._load_proposal_row(conn, proposal_id),
                    finalizer_teammate_id=carol_teammate_id,
                    author_private_key=carol_private,
                    author_public_key=carol_public,
                    anchor_commit=None,
                    endorsement_count=2,
                )
        finally:
            engine.dispose()

        # Variant 3 (crossed author/device): claims Alice as author but names
        # -- and is validly signed by -- Carol's device. Only the
        # device-belongs-to-teammate binding against the proposal snapshot
        # rejects it.
        carol_device_key_id = key_id_from_public(carol_public)
        with sqlite3.connect(str(db)) as conn:
            alice_teammate_id = conn.execute(
                "SELECT id FROM teammate WHERE display_name = 'Alice'"
            ).fetchone()[0]
            proposal_digest, snapshot_json = conn.execute(
                "SELECT constitution_digest, constitution_snapshot_json "
                "FROM admission_proposal WHERE record_id = ?",
                (proposal_id,),
            ).fetchone()
            subject_digest = conn.execute(
                "SELECT subject_digest FROM endorsement WHERE subject_record_id = ?",
                (proposal_id,),
            ).fetchone()[0]
            now = provisioning._now_iso()
            crossed_signed = {
                **provisioning._record_envelope_fields(
                    record_type="finalization",
                    author_teammate_id=alice_teammate_id,
                    author_device_key_id=carol_device_key_id,
                    created_at=now,
                    anchor_commit=None,
                    constitution_digest=proposal_digest,
                ),
                "subject_record_id": proposal_id.hex(),
                "subject_digest": subject_digest.hex(),
                "endorsement_count": 2,
            }
            crossed_canonical = canonical_constitution_bytes(crossed_signed)
            conn.execute(
                "INSERT INTO finalization ("
                "record_id, record_type, author_teammate_id, author_device_key_id, created_at, "
                "anchor_commit, constitution_digest, constitution_snapshot_json, schema_version, "
                "subject_record_id, subject_digest, endorsement_count, signature) "
                "VALUES (?, 'finalization', ?, ?, ?, NULL, ?, ?, 1, ?, ?, 2, ?)",
                (
                    derive_record_id(crossed_canonical),
                    alice_teammate_id,
                    carol_device_key_id,
                    now,
                    proposal_digest,
                    snapshot_json,
                    proposal_id,
                    subject_digest,
                    sign_constitution_record(carol_private, crossed_canonical),
                ),
            )
            conn.commit()

    _insert_forged_finalizations()

    # Neither forged row is terminal: the proposal still awaits quorum...
    statuses = {
        p["id"]: p["status"] for p in provisioning.list_invitations(root, alice_hex, "ProjectX")
    }
    assert statuses[proposal_hex] == "awaiting_quorum"
    # ...and the block reason is the honest one (quorum), not "already finalized".
    with pytest.raises(ValueError, match="quorum"):
        provisioning.finalize_admission(root, alice_hex, "ProjectX", proposal_hex)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM teammate WHERE display_name = 'Bob'"
        ).fetchone()[0] == 0

    # Meet quorum for real; the legitimate finalization coexists with the
    # forged rows (no UNIQUE on subject_record_id) and wins.
    provisioning.endorse_admission(root, carol_hex, "ProjectX", proposal_hex)
    shutil.copy2(carol_sync / "core.db", alice_sync / "core.db")
    _insert_forged_finalizations()
    provisioning.finalize_admission(root, alice_hex, "ProjectX", proposal_hex)

    statuses = {
        p["id"]: p["status"] for p in provisioning.list_invitations(root, alice_hex, "ProjectX")
    }
    assert statuses[proposal_hex] == "finalized"
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM finalization WHERE subject_record_id = ?", (proposal_id,)
        ).fetchone()[0] == 4
        assert conn.execute(
            "SELECT COUNT(*) FROM teammate WHERE display_name = 'Bob'"
        ).fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# 12. Crossed author/device on the proposal itself: a row claiming Alice as
#     author but validly signed by Carol's device is rejected -- the device
#     must belong to the claimed teammate (bound via team_device, since the
#     proposal's own snapshot is self-referential).
# --------------------------------------------------------------------------- #


def test_proposal_claiming_another_teammates_authorship_is_rejected(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    carol_hex = provisioning.create_new_participant(root, "Carol")
    _admit(root, alice_hex, carol_hex, cloud, label="Carol")

    team_id = provisioning._team_row(root, alice_hex, "ProjectX")[0]
    carol_private, carol_public = provisioning.get_current_team_device_key(
        root, carol_hex, "ProjectX"
    )
    carol_device_key_id = key_id_from_public(carol_public)
    mode_plan = provisioning.mode_plan_for_preset("steward")
    nonce = b"\x01" * 16
    expires_at = "2999-01-01T00:00:00+00:00"

    engine = provisioning._sqlite_engine(alice_sync / "core.db")
    try:
        with engine.begin() as conn:
            alice_teammate_id = conn.execute(
                provisioning.text("SELECT id FROM teammate WHERE display_name = 'Alice'")
            ).fetchone()[0]
            snapshot = provisioning._constitution_snapshot(conn)
            digest = provisioning._constitution_digest(snapshot)
            now = provisioning._now_iso()
            invitee_teammate_id = provisioning.uuid7()
            signed = {
                **provisioning._record_envelope_fields(
                    record_type="admission_proposal",
                    author_teammate_id=alice_teammate_id,   # claimed author
                    author_device_key_id=carol_device_key_id,  # Carol's device
                    created_at=now,
                    anchor_commit=None,
                    constitution_digest=digest,
                ),
                "nonce": nonce.hex(),
                "team_id": team_id.hex(),
                "invitee_teammate_id": invitee_teammate_id.hex(),
                "invitee_label_commitment": None,
                "expires_at": expires_at,
                "mode_plan": mode_plan,
            }
            canonical = canonical_constitution_bytes(signed)
            # In-memory row tuple in _load_proposal_row column order: internally
            # self-consistent (record_id and signature both valid) -- only the
            # device-to-teammate binding is wrong.
            forged_row = (
                derive_record_id(canonical),
                alice_teammate_id,
                carol_device_key_id,
                now,
                None,
                digest,
                provisioning._json_dumps_sorted(snapshot),
                nonce,
                team_id,
                invitee_teammate_id,
                None,
                expires_at,
                sign_constitution_record(carol_private, canonical),
                None,
                provisioning._json_dumps_sorted(mode_plan),
            )
            with pytest.raises(ValueError, match="does not belong to the author teammate"):
                provisioning._verify_proposal_row(conn, forged_row)
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# 13. Committee round 5: finalization status verifies the proposal row itself
#     before trusting its author/snapshot. An in-place tamper of the proposal
#     (same record_id PK, swapped author) re-roots the device binding at
#     attacker-chosen data; without _verify_proposal_row in the status path, a
#     validly signed finalization from the new "author" reads as terminal.
# --------------------------------------------------------------------------- #


def test_tampered_proposal_author_does_not_enable_finalization(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    carol_hex = provisioning.create_new_participant(root, "Carol")
    _admit(root, alice_hex, carol_hex, cloud, label="Carol")  # insider attacker
    provisioning.set_team_admission_policy(root, alice_hex, "ProjectX", quorum=2)
    _push(alice_sync, cloud)

    token = provisioning.create_invitation(
        root, alice_hex, "ProjectX",
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_remote=CS.LocalFolderRemote(str(cloud))
    )
    provisioning.complete_invitation_acceptance(root, alice_hex, "ProjectX", acceptance)
    proposals = provisioning.list_invitations(root, alice_hex, "ProjectX")
    proposal_hex = next(p["id"] for p in proposals if p["status"] == "awaiting_quorum")
    proposal_id = bytes.fromhex(proposal_hex)

    # In-place tamper: same record_id PK (FKs intact), author swapped to Carol.
    # The proposal's stored snapshot already lists Carol's device (she is a
    # teammate), so the single-column swap suffices for an insider; an outsider
    # would additionally rewrite digest+snapshot to a self-consistent crafted
    # pair -- the same record_id-vs-canonical check catches both.
    db = alice_sync / "core.db"
    carol_private, carol_public = provisioning.get_current_team_device_key(
        root, carol_hex, "ProjectX"
    )
    carol_device_key_id = key_id_from_public(carol_public)
    with sqlite3.connect(str(db)) as conn:
        carol_teammate_id = conn.execute(
            "SELECT id FROM teammate WHERE display_name = 'Carol'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE admission_proposal SET author_teammate_id = ? WHERE record_id = ?",
            (carol_teammate_id, proposal_id),
        )
        proposal_digest = conn.execute(
            "SELECT constitution_digest FROM admission_proposal WHERE record_id = ?",
            (proposal_id,),
        ).fetchone()[0]
        snapshot_json = conn.execute(
            "SELECT constitution_snapshot_json FROM admission_proposal WHERE record_id = ?",
            (proposal_id,),
        ).fetchone()[0]
        subject_digest = conn.execute(
            "SELECT subject_digest FROM endorsement WHERE subject_record_id = ?",
            (proposal_id,),
        ).fetchone()[0]
        # Carol finalizes "her own" proposal: valid signature, valid device,
        # author matches the (tampered) proposal author.
        now = provisioning._now_iso()
        signed = {
            **provisioning._record_envelope_fields(
                record_type="finalization",
                author_teammate_id=carol_teammate_id,
                author_device_key_id=carol_device_key_id,
                created_at=now,
                anchor_commit=None,
                constitution_digest=proposal_digest,
            ),
            "subject_record_id": proposal_id.hex(),
            "subject_digest": subject_digest.hex(),
            "endorsement_count": 2,
        }
        canonical = canonical_constitution_bytes(signed)
        conn.execute(
            "INSERT INTO finalization ("
            "record_id, record_type, author_teammate_id, author_device_key_id, created_at, "
            "anchor_commit, constitution_digest, constitution_snapshot_json, schema_version, "
            "subject_record_id, subject_digest, endorsement_count, signature) "
            "VALUES (?, 'finalization', ?, ?, ?, NULL, ?, ?, 1, ?, ?, 2, ?)",
            (
                derive_record_id(canonical),
                carol_teammate_id,
                carol_device_key_id,
                now,
                proposal_digest,
                snapshot_json,
                proposal_id,
                subject_digest,
                sign_constitution_record(carol_private, canonical),
            ),
        )
        conn.commit()

    # The tampered proposal fails record_id-vs-canonical verification, so the
    # finalization -- however well signed -- is not terminal.
    statuses = {
        p["id"]: p["status"] for p in provisioning.list_invitations(root, alice_hex, "ProjectX")
    }
    assert statuses[proposal_hex] == "awaiting_quorum"
    with pytest.raises(ValueError, match="Only the inviter|record_id does not match"):
        provisioning.finalize_admission(root, alice_hex, "ProjectX", proposal_hex)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM teammate WHERE display_name = 'Bob'"
        ).fetchone()[0] == 0
