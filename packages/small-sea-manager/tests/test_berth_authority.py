"""Micro tests for the transitional berth-authority view and evaluator (#266)."""

import os
import pathlib
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import small_sea_manager.provisioning as provisioning
from cod_sync.repo import Repo
from cod_sync.verify import SignatureEvidence, SignatureEvidenceKind, SshCommitVerifier
from cod_sync.work_context import WorkContext, commit_message_with_context, context_from_commit
from small_sea_manager.berth_authority import (
    AuthorityResult as R,
    ModeChangeRecord,
    build_view,
    evaluate,
    sign_workhorse_delegation,
    ssh_fingerprint,
)
from wrasse_trust.constitution import sign_constitution_record
from wrasse_trust.identity import issue_membership_cert
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

    def view(self, anchor=None):
        return build_view(team_id=TEAM, anchor_public_key=anchor or self.alice.public_key,
                          certs=self.certs, mode_changes=self.modes,
                          delegations=self.delegations, berth_ids=[BERTH, OTHER_BERTH])

    def delegate(self, teammate_id, private, purposes, berth=BERTH):
        _private, public = _workhorse()
        self.delegations.append(sign_workhorse_delegation(
            team_id=TEAM, berth_id=berth, purposes=purposes, workhorse_public_key=public,
            delegator_teammate_id=teammate_id, delegator_private_key=private))
        return public


def _judge(view, workhorse_public, purpose="files-content", kind=SignatureEvidenceKind.KNOWN_KEY,
           context_berth=BERTH):
    evidence = SignatureEvidence("c0ffee", kind, ssh_fingerprint(workhorse_public), "G")
    context = WorkContext("commit", TEAM.hex(), context_berth.hex(), purpose, b"signer-view")
    return evaluate(view, context, evidence, team_id=TEAM, berth_id=BERTH, purpose=purpose)


def test_bootstrap_grant_scope():
    team = Team()
    alice_key = team.delegate(team.alice_id, team.alice_private, ["files-content"])
    # Identity-only enrollment: Bob is a trusted member but holds no berth.
    bob_key = team.delegate(team.bob_id, team.bob_private, ["files-content"])
    view = team.view()
    assert _judge(view, alice_key).result is R.AUTHORIZED
    assert _judge(view, bob_key).result is R.MISSING_AUTHORITY
    assert _judge(view, alice_key, purpose="files-registry").result is R.WRONG_SCOPE

    # A forged genesis: Mallory self-issues a membership and delegates to herself.
    mallory, mallory_private = _device()
    mallory_id = b"M" * 16
    team.certs.append(issue_membership_cert(mallory, mallory, mallory_private, TEAM,
                                            mallory_id, mallory_id))
    mallory_key = team.delegate(mallory_id, mallory_private, ["files-content"])
    assert _judge(team.view(), mallory_key).result is R.MISSING_AUTHORITY
    # Adopting a different anchor is a different view, not a verification of Alice's.
    assert team.view(anchor=mallory.public_key).identifier != team.view().identifier


def test_each_result_is_reachable():
    team = Team()
    alice_key = team.delegate(team.alice_id, team.alice_private, ["files-content"])
    other_key = team.delegate(team.alice_id, team.alice_private, ["files-content"], berth=OTHER_BERTH)
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
    bob_key = team.delegate(team.bob_id, team.bob_private, ["files-content"])
    assert _judge(team.view(), bob_key).result is R.AUTHORIZED
    team.modes.append(_mode(team.alice_id, team.alice, team.alice_private, team.bob_id, BERTH, "proposal-only"))
    decision = _judge(team.view(), bob_key)
    assert decision.result is R.AMBIGUOUS_AUTHORITY
    assert decision.view_identifier == team.view().identifier


def test_view_identifier_tracks_only_relevant_records():
    team = Team()
    team.delegate(team.alice_id, team.alice_private, ["files-content"])
    before = team.view().identifier
    assert before.startswith(b"ssc-transitional-authority-view/1:")
    assert team.view().identifier == before

    # Records that do not enter the view leave its identifier alone.
    team.delegate(team.bob_id, team.bob_private, ["files-content"])  # Bob holds nothing
    mallory, mallory_private = _device()
    team.certs.append(issue_membership_cert(mallory, mallory, mallory_private, TEAM, b"M" * 16, b"M" * 16))
    tampered = team.delegations[0]
    team.delegations.append(type(tampered)(**{**tampered.__dict__, "purposes": ("files-merge",)}))
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
    _private, anchor = provisioning.get_current_team_device_key(root, participant, "ProjectX")
    team_id, _ = provisioning._team_row(root, participant, "ProjectX")

    before = provisioning.load_transitional_authority_view(root, participant, "ProjectX", anchor)
    assert before.delegations == ()
    provisioning.delegate_workhorse_purposes(root, participant, "ProjectX", berth, ["files-content"], anchor)
    view = provisioning.load_transitional_authority_view(root, participant, "ProjectX", anchor)
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
    context = WorkContext("commit", team_id.hex(), berth.hex(), "files-content", view.identifier)
    sha = repo.commit(commit_message_with_context("write a", context))

    evidence = SshCommitVerifier(view.workhorse_public_keys()).signature_evidence(repo, sha)[sha]
    decision = evaluate(view, context_from_commit(repo, sha), evidence,
                        team_id=team_id, berth_id=berth, purpose="files-content")
    assert decision.result is R.AUTHORIZED
    assert evaluate(view, context_from_commit(repo, sha), evidence, team_id=team_id,
                    berth_id=berth, purpose="files-merge").result is R.WRONG_SCOPE
