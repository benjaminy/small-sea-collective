"""Micro tests for the transitional berth-authority view and evaluator (#266)."""

import os
import json
import pathlib
import sqlite3
import subprocess

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import small_sea_manager.provisioning as provisioning
from cod_sync.repo import Repo
from small_sea_note_to_self.db import device_local_db_path
from cod_sync.verify import SignatureEvidence, SignatureEvidenceKind, SshCommitVerifier
from cod_sync.work_context import WorkContext, commit_message_with_context, context_from_commit
from small_sea_manager.berth_authority import (
    AuthorityResult as R,
    DELEGATION_VERSION,
    MissingAuthorityAnchor,
    Standing,
    TeammateRemoval,
    ModeChangeRecord,
    build_view,
    evaluate,
    sign_workhorse_delegation,
    ssh_fingerprint,
)
from wrasse_trust.constitution import sign_constitution_record
from wrasse_trust.identity import issue_membership_cert, issue_device_link_cert
from wrasse_trust.keys import ProtectionLevel, generate_key_pair

TEAM = b"T" * 16
BERTH = b"F" * 16
OTHER_BERTH = b"G" * 16


def _device():
    key, private = generate_key_pair(ProtectionLevel.DAILY)
    return key, private


def _workhorse():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()
    return private, public


def _mode(author_id, author, author_private, teammate_id, berth, mode):
    record = ModeChangeRecord(author_id, author.key_id, "2026-09-26T00:00:00+00:00", None,
                              b"\0" * 32, 1, teammate_id, berth, mode, b"")
    return ModeChangeRecord(**{**record.__dict__,
                               "signature": sign_constitution_record(author_private, record.canonical())})


class Team:
    """Founder Alice (anchor) and Bob, enrolled by Alice with no berth grant."""

    def __init__(self):
        self.alice, self.alice_private = _device()
        self.bob, self.bob_private = _device()
        self.alice_id, self.bob_id = b"A" * 16, b"B" * 16
        self.certs = [
            issue_membership_cert(self.alice, self.alice, self.alice_private, TEAM,
                                  self.alice_id, self.alice_id),
            issue_membership_cert(self.bob, self.alice, self.alice_private, TEAM,
                                  self.alice_id, self.bob_id),
        ]
        self.modes = []
        self.delegations = []

    def view(self, anchor=None, berth_ids=(BERTH, OTHER_BERTH), removals=(), core_berth_id=OTHER_BERTH):
        return build_view(team_id=TEAM, anchor_public_key=anchor or self.alice.public_key,
                          certs=self.certs, mode_changes=self.modes,
                          delegations=self.delegations, berth_ids=berth_ids,
                          removals=removals, core_berth_id=core_berth_id)

    def delegate(self, teammate_id, private, berth=BERTH):
        _private, public = _workhorse()
        self.delegations.append(sign_workhorse_delegation(
            team_id=TEAM, berth_id=berth, workhorse_public_key=public,
            delegator_teammate_id=teammate_id, delegator_private_key=private))
        return public


def _judge(view, workhorse_public, purpose="SmallSeaCollectiveFiles/content", kind=SignatureEvidenceKind.KNOWN_KEY,
           context_berth=BERTH):
    evidence = SignatureEvidence("c0ffee", kind, ssh_fingerprint(workhorse_public), "G")
    context = WorkContext("commit", TEAM.hex(), context_berth.hex(), purpose, b"signer-view")
    return evaluate(view, context, evidence, team_id=TEAM, berth_id=BERTH, purpose=purpose)


def test_bootstrap_grant_scope():
    team = Team()
    alice_key = team.delegate(team.alice_id, team.alice_private)
    # Identity-only enrollment: Bob is a trusted member but holds no berth.
    bob_key = team.delegate(team.bob_id, team.bob_private)
    view = team.view()
    assert _judge(view, alice_key).result is R.AUTHORIZED
    assert _judge(view, bob_key).result is R.MISSING_AUTHORITY

    # A forged genesis: Mallory self-issues a membership and delegates to herself.
    mallory, mallory_private = _device()
    mallory_id = b"M" * 16
    team.certs.append(issue_membership_cert(mallory, mallory, mallory_private, TEAM,
                                            mallory_id, mallory_id))
    mallory_key = team.delegate(mallory_id, mallory_private)
    assert _judge(team.view(), mallory_key).result is R.MISSING_AUTHORITY
    # Adopting a different anchor is a different view, not a verification of Alice's.
    assert team.view(anchor=mallory.public_key).identifier != team.view().identifier


def test_delegation_authorizes_any_valid_purpose_on_berth():
    team = Team()
    key = team.delegate(team.alice_id, team.alice_private)
    view = team.view()
    assert _judge(view, key, purpose="SmallSeaCollectiveFiles/content").result is R.AUTHORIZED
    assert _judge(view, key, purpose="SmallSeaCollectiveFiles/registry").result is R.AUTHORIZED
    assert _judge(view, key, purpose="invalid purpose").result is R.WRONG_SCOPE


def test_delegation_does_not_cover_other_berth():
    team = Team()
    key = team.delegate(team.alice_id, team.alice_private, berth=OTHER_BERTH)
    assert _judge(team.view(), key).result is R.WRONG_SCOPE


def test_delegation_signed_bytes_have_no_purposes():
    team = Team()
    team.delegate(team.alice_id, team.alice_private)
    delegation = team.delegations[0]
    signed = json.loads(delegation.canonical())
    assert "purposes" not in signed
    assert signed["version"] == DELEGATION_VERSION
    assert delegation.signature_valid()
    signed["version"] += 1
    public = Ed25519PrivateKey.from_private_bytes(team.alice_private).public_key()
    from cryptography.exceptions import InvalidSignature
    with pytest.raises(InvalidSignature):
        public.verify(delegation.signature, json.dumps(
            signed, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii"))


def test_each_result_is_reachable():
    team = Team()
    alice_key = team.delegate(team.alice_id, team.alice_private)
    other_key = team.delegate(team.alice_id, team.alice_private, berth=OTHER_BERTH)
    view = team.view()
    assert _judge(view, alice_key).result is R.AUTHORIZED
    for kind in (SignatureEvidenceKind.UNSIGNED, SignatureEvidenceKind.INVALID):
        assert _judge(view, alice_key, kind=kind).result is R.BAD_SIGNATURE
    assert _judge(view, alice_key, kind=SignatureEvidenceKind.UNKNOWN_SIGNER).result is R.MISSING_AUTHORITY
    assert _judge(view, alice_key, context_berth=OTHER_BERTH).result is R.WRONG_SCOPE
    assert _judge(view, other_key).result is R.WRONG_SCOPE
    assert _judge(view, _workhorse()[1]).result is R.MISSING_AUTHORITY

    # Alice grants Bob the berth, then a second record says proposal-only.
    # Without a Constitution DAG the two cannot be ordered: ambiguous.
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private, team.bob_id, BERTH, "automatic"))
    bob_key = team.delegate(team.bob_id, team.bob_private)
    assert _judge(team.view(), bob_key).result is R.AUTHORIZED
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private, team.bob_id, BERTH, "proposal-only"))
    decision = _judge(team.view(), bob_key)
    assert decision.result is R.AMBIGUOUS_AUTHORITY
    assert decision.view_identifier == team.view().identifier


def test_view_identifier_tracks_only_relevant_records():
    team = Team()
    team.delegate(team.alice_id, team.alice_private)
    before = team.view().identifier
    assert before.startswith(b"ssc-transitional-authority-view/1:")
    assert team.view().identifier == before

    # Records that do not enter the view leave its identifier alone.
    team.delegate(team.bob_id, team.bob_private)  # Bob holds nothing
    mallory, mallory_private = _device()
    team.certs.append(issue_membership_cert(mallory, mallory, mallory_private, TEAM, b"M" * 16, b"M" * 16))
    tampered = team.delegations[0]
    team.delegations.append(type(tampered)(**{**tampered.__dict__, "berth_id": OTHER_BERTH,}))
    team.certs.reverse()
    assert team.view().identifier == before

    # A new accepted record changes it.
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private, team.bob_id, BERTH, "automatic"))
    assert team.view().identifier != before


def test_manager_stores_self_delegation_in_team_core(playground_dir, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    root = pathlib.Path(playground_dir)
    participant = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, participant, "ProjectX")
    provisioning.register_app_for_participant(root, participant, "SmallSeaCollectiveFiles")
    provisioning.activate_app_for_team(root, participant, "ProjectX", "SmallSeaCollectiveFiles")
    berth = provisioning.derive_team_join_state(root, participant, "ProjectX", "SmallSeaCollectiveFiles")["berth_id"]
    berth = bytes.fromhex(berth) if isinstance(berth, str) else berth
    _private, genesis = provisioning.get_current_team_device_key(root, participant, "ProjectX")
    team_id, _ = provisioning._team_row(root, participant, "ProjectX")
    anchor = provisioning.get_authority_anchor(root, participant, team_id)
    assert anchor["anchor_public_key"] == genesis
    assert anchor["adopted_via"] == "team-creation"
    assert anchor["enrollment_completed_at"] is not None

    before = provisioning.load_transitional_authority_view(root, participant, "ProjectX")
    assert before.delegations == ()
    provisioning.delegate_workhorse_key(root, participant, "ProjectX", berth)
    view = provisioning.load_transitional_authority_view(root, participant, "ProjectX")
    assert view.identifier != before.identifier
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                            cwd=root / "Participants" / participant / "ProjectX" / "Sync").stdout
    assert status == ""  # committed into Core, which ordinary Core sync publishes

    key_path = root / "workhorse"
    key_path.write_bytes(Ed25519PrivateKey.from_private_bytes(
        provisioning.get_workhorse_signing_key(root, participant, berth)
    ).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                    serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    repo = Repo.init(root / "repo" / ".git").with_work_tree(root / "repo")
    repo.config("user.email", "a@test")
    repo.config("user.name", "a")
    repo.configure_signing(key_path)
    (root / "repo" / "a.txt").write_text("a")
    repo.stage(["a.txt"])
    context = WorkContext("commit", team_id.hex(), berth.hex(), "SmallSeaCollectiveFiles/content", view.identifier)
    sha = repo.commit(commit_message_with_context("write a", context))

    evidence = SshCommitVerifier(view.workhorse_public_keys()).signature_evidence(repo, sha)[sha]
    decision = evaluate(view, context_from_commit(repo, sha), evidence,
                        team_id=team_id, berth_id=berth, purpose="SmallSeaCollectiveFiles/content")
    assert decision.result is R.AUTHORIZED
    assert evaluate(view, context_from_commit(repo, sha), evidence, team_id=team_id,
                    berth_id=berth, purpose="SmallSeaCollectiveFiles/merge").result is R.WRONG_SCOPE


def test_self_grants_and_cycles_do_not_escape_ambiguity():
    team = Team()
    carol, carol_private = _device()
    carol_id = b"C" * 16
    team.certs.append(issue_membership_cert(carol, team.alice, team.alice_private, TEAM,
                                            team.alice_id, carol_id))
    grant = lambda author_id, author, private, to, mode: team.modes.append(
        _mode(author_id, author, private, to, BERTH, mode))
    grant(team.alice_id, team.alice, team.alice_private, team.bob_id, "automatic")
    grant(team.alice_id, team.alice, team.alice_private, team.bob_id, "proposal-only")
    grant(team.bob_id, team.bob, team.bob_private, carol_id, "automatic")
    grant(carol_id, carol, carol_private, carol_id, "automatic")
    # A cycle: Carol grants Bob back; Bob is still directly conflicted.
    grant(carol_id, carol, carol_private, team.bob_id, "automatic")
    carol_key = team.delegate(carol_id, carol_private)
    view = team.view()
    assert view.holds(team.bob_id, BERTH) is Standing.AMBIGUOUS
    assert view.holds(carol_id, BERTH) is Standing.AMBIGUOUS
    assert _judge(view, carol_key).result is R.AMBIGUOUS_AUTHORITY


def test_view_identifier_covers_the_berth_set():
    team = Team()
    assert team.view(berth_ids=(), core_berth_id=None).identifier != team.view(berth_ids=(BERTH,), core_berth_id=None).identifier


def test_absent_anchor_pauses(playground_dir):
    root = pathlib.Path(playground_dir)
    participant = provisioning.create_new_participant(root, "Alice")
    provisioning.create_team(root, participant, "ProjectX")
    team_id, _ = provisioning._team_row(root, participant, "ProjectX")
    with sqlite3.connect(device_local_db_path(root, participant)) as conn:
        conn.execute("DELETE FROM team_authority_anchor WHERE team_id = ?", (team_id,))
    with pytest.raises(MissingAuthorityAnchor):
        provisioning.load_transitional_authority_view(root, participant, "ProjectX")
    berth = provisioning.derive_team_join_state(root, participant, "ProjectX")["berth_id"]
    with pytest.raises(MissingAuthorityAnchor):
        provisioning.delegate_workhorse_key(root, participant, "ProjectX", berth)


def test_linked_device_adopts_anchor_from_signed_bootstrap(playground_dir, minio_server_gen):
    from test_linked_device_cold_start import TEAM as LINKED_TEAM, _discovered_team_on_device_b

    root_a, root_b, alice_hex, manager_a, manager_b = _discovered_team_on_device_b(
        pathlib.Path(playground_dir), minio_server_gen()
    )
    team_id, _ = provisioning._team_row(root_b, alice_hex, LINKED_TEAM)
    assert provisioning.get_authority_anchor(root_b, alice_hex, team_id) is None
    with pytest.raises(MissingAuthorityAnchor):
        provisioning.delegate_workhorse_key(root_b, alice_hex, LINKED_TEAM, b"x" * 16)

    prepared = manager_b.prepare_linked_device_team_join(LINKED_TEAM)
    created = manager_a.create_linked_device_bootstrap(LINKED_TEAM, prepared["join_request_bundle"])
    manager_b.finalize_linked_device_bootstrap(LINKED_TEAM, created["bootstrap_bundle"])

    adopted = provisioning.get_authority_anchor(root_b, alice_hex, team_id)
    founder = provisioning.get_authority_anchor(root_a, alice_hex, team_id)
    assert adopted["anchor_public_key"] == founder["anchor_public_key"]
    assert adopted["adopted_via"] == "linked-device-bootstrap"
    assert adopted["enrollment_completed_at"] is not None
    view_a = provisioning.load_transitional_authority_view(root_a, alice_hex, LINKED_TEAM)
    view_b = provisioning.load_transitional_authority_view(root_b, alice_hex, LINKED_TEAM)
    assert view_a.identifier == view_b.identifier


def _removal(team, author=None, signer=None, target=None, event_id=b"R" * 32):
    return TeammateRemoval(
        author or team.alice_id, signer or team.alice.public_key,
        target or team.bob_id, event_id,
    )


def test_removal_envelope_shape_is_checked():
    from small_sea_manager.constitution_projection import ProjectionError, check_event, apply_event
    from wrasse_trust.events import make_event
    team = Team()
    payload = dict(version=1, team_id=TEAM.hex(),
                   author_teammate_id=team.alice_id.hex(), teammate_id=team.bob_id.hex())
    event = make_event("teammate_removed", payload, (), team.alice_private)
    check_event(event)
    apply_event(None, event)  # A removal writes no projection rows.
    malformed = [{k: v for k, v in payload.items() if k != field} for field in payload]
    malformed += [{**payload, "version": v} for v in (True, 2, "1", None)]
    malformed += [{**payload, field: v}
                  for field in ("team_id", "author_teammate_id", "teammate_id")
                  for v in ("", "not hex", None, 12, [])]
    malformed += [{**payload, "extra": 1}]
    for bad in malformed:
        with pytest.raises(ProjectionError, match="bad_removal_payload"):
            check_event(make_event("teammate_removed", bad, (), team.alice_private))


def test_unauthorized_removal_has_no_effect():
    team = Team()
    removal = _removal(team, author=team.bob_id, signer=team.bob.public_key,
                       target=team.alice_id)
    assert team.view(removals=[removal]) == team.view()
    # Files standing does not confer Core standing.
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private,
                            team.bob_id, BERTH, "automatic"))
    assert team.view(removals=[removal]) == team.view()
    for mode in ("automatic", "proposal-only"):
        team.modes.append(_mode(team.alice_id, team.alice, team.alice_private,
                                team.bob_id, OTHER_BERTH, mode))
    assert team.view(removals=[removal]) == team.view()
    assert team.view(removals=[_removal(team)], core_berth_id=None) == team.view(core_berth_id=None)


def test_removal_by_untrusted_key_has_no_effect():
    team = Team()
    assert team.view(removals=[_removal(team, signer=_device()[0].public_key)]) == team.view()


def test_self_removal_has_no_effect():
    team = Team()
    assert team.view(removals=[_removal(team, target=team.alice_id)]) == team.view()


def test_effective_removal_suppresses_target_keys_and_grants():
    team = Team()
    linked, _private = _device()
    team.certs.append(issue_device_link_cert(linked, team.bob, team.bob_private,
                                             TEAM, team.bob_id))
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private,
                            team.bob_id, BERTH, "automatic"))
    public = team.delegate(team.bob_id, team.bob_private)
    assert linked.public_key in team.view().trusted_keys[team.bob_id]
    assert public in team.view().workhorse_public_keys()
    view = team.view(removals=[_removal(team)])
    assert team.bob_id not in view.trusted_keys
    assert view.holds(team.bob_id, BERTH) is None
    assert view.delegations == ()


def test_removal_withdraws_grants_issued_by_target():
    team = Team()
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private,
                            team.bob_id, BERTH, "automatic"))
    for teammate_id in (b"C" * 16, b"D" * 16, b"E" * 16):
        key, _private = _device()
        issuer, private, issuer_id = (
            (team.bob, team.bob_private, team.bob_id) if teammate_id == b"E" * 16
            else (team.alice, team.alice_private, team.alice_id))
        team.certs.append(issue_membership_cert(key, issuer, private, TEAM, issuer_id, teammate_id))
        team.modes.append(_mode(team.bob_id, team.bob, team.bob_private,
                                teammate_id, BERTH, "automatic"))
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private,
                            b"D" * 16, BERTH, "automatic"))
    assert team.view().holds(b"C" * 16, BERTH) is Standing.HELD
    view = team.view(removals=[_removal(team)])
    assert view.holds(b"C" * 16, BERTH) is None
    assert view.holds(b"D" * 16, BERTH) is Standing.HELD
    assert b"E" * 16 not in view.trusted_keys


def test_late_certificate_cannot_restore_removed_teammate():
    team = Team()
    removal = _removal(team)
    before = team.view(removals=[removal])
    later, _private = _device()
    team.certs.append(issue_membership_cert(later, team.alice, team.alice_private,
                                            TEAM, team.alice_id, team.bob_id))
    assert team.view(removals=[removal]) == before


def test_removal_changes_view_identifier():
    team = Team()
    first = _removal(team)
    second = _removal(team, event_id=b"S" * 32)
    assert team.view(removals=[first]).identifier != team.view().identifier
    assert team.view(removals=[first]).identifier != team.view(removals=[second]).identifier
    assert team.view(removals=[first, second]) == team.view(removals=[second, first, first])
