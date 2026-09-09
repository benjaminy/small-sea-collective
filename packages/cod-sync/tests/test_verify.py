"""Micro tests for Cod Sync's acceptance boundary when a verifier is supplied.

Cover rejection before refs move, retries over imported objects, original
ancestry, and verification setup failures, with passing controls.
"""

import pathlib
import subprocess

import pytest
from cod_sync_test_helpers import (
    all_refs,
    commit_file,
    isolated_git_config,  # noqa: F401  (pytest fixture)
    make_cod_sync,
    make_repo,
    make_ssh_key,
    make_store,
    public_key_text,
)

from cod_sync.protocol import MAIN_REF, PublicationIntegrationRequiredError
from cod_sync.verify import (
    SignatureInvalidError,
    SshCommitVerifier,
    UnknownSignerError,
    UnsignedCommitError,
    VerificationUnavailableError,
)

pytestmark = pytest.mark.usefixtures("isolated_git_config")

PIN = "refs/peers/alice/main"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def verifier_for(*keys):
    return SshCommitVerifier([public_key_text(key) for key in keys])


def publisher(scratch, key=None, name="alice"):
    """A repo that signs with key, if one is given."""
    repo = make_repo(scratch / name, name)
    if key is not None:
        repo.configure_signing(key)
    return repo


def publish(repo, publication):
    return make_cod_sync(repo, make_store(publication)).publish()


def stored_files(publication):
    return sorted(p.name for p in pathlib.Path(publication).rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# The boundary itself
# ---------------------------------------------------------------------------


def test_a_signed_history_is_accepted(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch, key)
    commit_file(alice, "a.txt", "a")
    head = publish(alice, scratch / "publication").observed_head

    bob = make_repo(scratch / "bob", "bob")
    result = make_cod_sync(bob, make_store(scratch / "publication"), verifier_for(key)).fetch(
        pin_to_ref=PIN
    )

    assert result.observed_head == head
    assert bob.resolve_ref(PIN) == head


def test_no_verifier_accepts_an_unsigned_history(scratch_dir):
    """The default. Nothing in the framework can force an app to verify."""
    scratch = pathlib.Path(scratch_dir)
    alice = publisher(scratch)
    commit_file(alice, "a.txt", "a")
    head = publish(alice, scratch / "publication").observed_head

    bob = make_repo(scratch / "bob", "bob")
    result = make_cod_sync(bob, make_store(scratch / "publication")).fetch(pin_to_ref=PIN)

    assert result.observed_head == head
    assert bob.resolve_ref(PIN) == head


def test_an_unsigned_history_is_rejected_before_the_pin_moves(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch)
    commit_file(alice, "a.txt", "a")
    publish(alice, scratch / "publication")

    bob = make_repo(scratch / "bob", "bob")
    before = all_refs(bob)
    cod_sync = make_cod_sync(bob, make_store(scratch / "publication"), verifier_for(key))

    with pytest.raises(UnsignedCommitError):
        cod_sync.fetch(pin_to_ref=PIN)

    assert bob.resolve_ref(PIN) is None
    assert all_refs(bob) == before


def test_an_unknown_signer_is_rejected(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    alice_key = make_ssh_key(scratch, "alice")
    mallory_key = make_ssh_key(scratch, "mallory")
    mallory = publisher(scratch, mallory_key, name="mallory")
    commit_file(mallory, "a.txt", "a")
    head = publish(mallory, scratch / "publication").observed_head

    bob = make_repo(scratch / "bob", "bob")
    store = make_store(scratch / "publication")
    with pytest.raises(UnknownSignerError):
        make_cod_sync(bob, store, verifier_for(alice_key)).fetch(pin_to_ref=PIN)
    assert bob.resolve_ref(PIN) is None

    assert (
        make_cod_sync(bob, store, verifier_for(mallory_key))
        .fetch(pin_to_ref=PIN)
        .observed_head
        == head
    )


def test_an_empty_key_set_recognizes_nobody(scratch_dir):
    """An intentionally empty set is a policy, not a broken verifier."""
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch, key)
    commit_file(alice, "a.txt", "a")
    publish(alice, scratch / "publication")

    bob = make_repo(scratch / "bob", "bob")
    with pytest.raises(UnknownSignerError):
        make_cod_sync(bob, make_store(scratch / "publication"), SshCommitVerifier([])).fetch()


def test_a_tampered_commit_is_rejected_as_a_bad_signature(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch, key)
    commit_file(alice, "a.txt", "a")
    genuine = commit_file(alice, "b.txt", "b", message="original")

    original = alice._run_binary(["cat-file", "commit", genuine]).stdout
    write = subprocess.run(
        ["git", "--git-dir", str(alice.git_dir), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=original.replace(b"original", b"tampered"),
        capture_output=True,
        check=True,
    )
    tampered = write.stdout.decode().strip()
    alice._run(["update-ref", MAIN_REF, tampered])
    publish(alice, scratch / "publication")

    bob = make_repo(scratch / "bob", "bob")
    with pytest.raises(SignatureInvalidError) as rejection:
        make_cod_sync(bob, make_store(scratch / "publication"), verifier_for(key)).fetch()

    assert rejection.value.commit == tampered
    assert rejection.value.status == "B"


def test_an_unsigned_ancestor_is_rejected_though_the_head_is_signed(scratch_dir):
    """The head is not the boundary: everything it relies on is."""
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch)
    ancestor = commit_file(alice, "a.txt", "a")
    alice.configure_signing(key)
    head = commit_file(alice, "b.txt", "b")
    publish(alice, scratch / "publication")

    bob = make_repo(scratch / "bob", "bob")
    with pytest.raises(UnsignedCommitError) as rejection:
        make_cod_sync(bob, make_store(scratch / "publication"), verifier_for(key)).fetch()

    assert rejection.value.commit == ancestor
    assert rejection.value.commit != head


@pytest.mark.parametrize("operation", ["fetch", "publish"])
def test_merge_side_ancestry_is_verified(scratch_dir, operation):
    """A merge's other parent is relied upon just as much as its first."""
    scratch = pathlib.Path(scratch_dir)
    alice_key = make_ssh_key(scratch, "alice")
    mallory_key = make_ssh_key(scratch, "mallory")

    alice = publisher(scratch, alice_key)
    base = commit_file(alice, "base.txt", "base")
    alice.checkout_branch("side", base)
    alice.configure_signing(mallory_key)
    side = commit_file(alice, "side.txt", "side")
    alice.checkout_branch("main", base)
    alice.configure_signing(alice_key)
    main_tip = commit_file(alice, "main.txt", "main")
    assert side != main_tip
    alice.merge(side)
    merge = alice.head()
    assert sorted(alice._run(["rev-list", "--parents", "-n", "1", merge]).stdout.split()[1:]) == (
        sorted([main_tip, side])
    )
    publish(alice, scratch / "publication")

    bob = make_repo(scratch / "bob", "bob")
    store = make_store(scratch / "publication")
    commit_file(bob, "local.txt", "local")
    sync = make_cod_sync(bob, store, verifier_for(alice_key))
    before = all_refs(bob)
    with pytest.raises(UnknownSignerError) as rejection:
        sync.fetch(pin_to_ref=PIN) if operation == "fetch" else sync.publish()

    assert all_refs(bob) == before
    assert rejection.value.commit == side
    assert bob.resolve_ref(PIN) is None
    sync.verifier = verifier_for(alice_key, mallory_key)
    if operation == "fetch":
        assert sync.fetch(pin_to_ref=PIN).observed_head == merge
    else:
        with pytest.raises(PublicationIntegrationRequiredError) as result:
            sync.publish()
        assert result.value.observed_head == merge



def test_a_retry_cannot_ride_on_the_objects_the_rejection_left_behind(scratch_dir):
    """`_already_satisfied` skips the import; it must not skip verification."""
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch)
    commit_file(alice, "a.txt", "a")
    head = publish(alice, scratch / "publication").observed_head

    bob = make_repo(scratch / "bob", "bob")
    cod_sync = make_cod_sync(bob, make_store(scratch / "publication"), verifier_for(key))
    with pytest.raises(UnsignedCommitError):
        cod_sync.fetch(pin_to_ref=PIN)

    assert bob.has_commit(head), "the rejected objects are present, which is the point"
    with pytest.raises(UnsignedCommitError):
        cod_sync.fetch(pin_to_ref=PIN)
    assert bob.resolve_ref(PIN) is None


def test_a_publication_uploads_nothing_when_the_stored_head_fails_verification(scratch_dir):
    """Observation is where a publication accepts the history it builds on."""
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    publication = scratch / "publication"
    alice = publisher(scratch)
    commit_file(alice, "a.txt", "a")
    publish(alice, publication)
    before = stored_files(publication)

    bob = publisher(scratch, key, name="bob")
    commit_file(bob, "b.txt", "b")
    with pytest.raises(UnsignedCommitError):
        make_cod_sync(bob, make_store(publication), verifier_for(key)).publish()

    assert stored_files(publication) == before


def test_a_status_this_verifier_does_not_accept_is_not_called_tampering(scratch_dir):
    """Anything but G is a rejection, and only B claims a cryptographic failure."""
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch, key)
    commit_file(alice, "a.txt", "a")

    verifier = SshCommitVerifier([public_key_text(key)])
    unknown_status = type(
        "OneStatus",
        (),
        {
            "signature_report": staticmethod(
                lambda rev, signers: (
                    [type("Row", (), {"commit": rev, "status": "R", "fingerprint": ""})()],
                    "",
                )
            )
        },
    )()

    with pytest.raises(VerificationUnavailableError) as rejection:
        verifier.verify_history(unknown_status, alice.head())
    assert rejection.value.status == "R"


@pytest.mark.parametrize("operation", ["fetch", "publish"])
@pytest.mark.parametrize("local_state", ["replace", "shallow"])
def test_local_history_overrides_cannot_hide_unsigned_ancestry(
    scratch_dir, operation, local_state
):
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    alice = publisher(scratch)
    root = commit_file(alice, "a.txt", "a")
    alice.configure_signing(key)
    head = commit_file(alice, "b.txt", "b")
    store = make_store(scratch / "publication")
    make_cod_sync(alice, store).publish()
    bob = publisher(scratch, key, "bob")
    make_cod_sync(bob, store).fetch()
    bob._run(["update-ref", MAIN_REF, head])
    if local_state == "replace":
        tree = bob._run(["rev-parse", f"{head}^{{tree}}"]).stdout.strip()
        replacement = bob.commit_tree(tree, [], "signed replacement")
        bob._run(["replace", root, replacement])
        error = UnsignedCommitError
    else:
        (bob.git_dir / "shallow").write_text(head + "\n")
        error = VerificationUnavailableError
    before = all_refs(bob)
    files = stored_files(scratch / "publication")
    sync = make_cod_sync(bob, store, verifier_for(key))
    with pytest.raises(error):
        sync.fetch(pin_to_ref=PIN) if operation == "fetch" else sync.publish()
    assert all_refs(bob) == before
    assert stored_files(scratch / "publication") == files

    # A complete, genuinely signed root remains verifiable.
    if local_state == "shallow":
        (bob.git_dir / "shallow").unlink()
    else:
        bob._run(["replace", "-d", root])
    tree = bob._run(["rev-parse", f"{head}^{{tree}}"]).stdout.strip()
    signed_root = bob.commit_tree(tree, [], "signed root")
    assert verifier_for(key).verify_history(bob, signed_root)[signed_root]


@pytest.mark.parametrize("operation", ["fetch", "publish"])
@pytest.mark.parametrize("preexisting", [False, True])
def test_incremental_rejection_and_retry_check_existing_objects(
    scratch_dir, monkeypatch, operation, preexisting
):
    scratch = pathlib.Path(scratch_dir)
    alice_key = make_ssh_key(scratch, "alice")
    other_key = make_ssh_key(scratch, "other")
    alice = publisher(scratch, other_key)
    base = commit_file(alice, "a.txt", "a")
    store = make_store(scratch / "publication")
    make_cod_sync(alice, store).publish()
    bob = publisher(scratch, alice_key, "bob")
    if preexisting:
        make_cod_sync(bob, store).fetch()
    alice.configure_signing(alice_key)
    head = commit_file(alice, "b.txt", "b")
    make_cod_sync(alice, store).publish()
    # A local head is required to exercise publication observation.
    commit_file(bob, "local.txt", "local")
    before = all_refs(bob)
    files = stored_files(scratch / "publication")
    sync = make_cod_sync(bob, store, verifier_for(alice_key))

    def unexpected_upload(*args, **kwargs):
        pytest.fail("rejected observation must not upload anything")

    for method in ("put_bundle", "put_link", "put_latest_link"):
        monkeypatch.setattr(store, method, unexpected_upload)
    for _ in range(2):
        with pytest.raises(UnknownSignerError) as rejection:
            sync.fetch(pin_to_ref=PIN) if operation == "fetch" else sync.publish()
        assert rejection.value.commit == base
        assert bob.has_commit(base) and bob.has_commit(head)
        assert all_refs(bob) == before
        assert stored_files(scratch / "publication") == files
    # Change only the recognized key set for the passing control.
    sync.verifier = verifier_for(alice_key, other_key)
    if operation == "fetch":
        assert sync.fetch(pin_to_ref=PIN).observed_head == head
    else:
        with pytest.raises(PublicationIntegrationRequiredError) as result:
            sync.publish()
        assert result.value.observed_head == head


@pytest.mark.parametrize("failure", ["missing_file", "missing_tool", "malformed_key"])
def test_verifier_setup_failures_are_not_unknown_signers(scratch_dir, monkeypatch, failure):
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    repo = publisher(scratch, key)
    head = commit_file(repo, "a.txt", "a")
    assert verifier_for(key).verify_history(repo, head)[head]
    original = repo.signature_report
    if failure == "missing_file":
        def report(rev, signers):
            pathlib.Path(signers).unlink()
            return original(rev, signers)
        monkeypatch.setattr(repo, "signature_report", report)
    elif failure == "missing_tool":
        repo.config("gpg.ssh.program", str(scratch / "missing-ssh-keygen"))
    with pytest.raises(VerificationUnavailableError):
        verifier = (
            SshCommitVerifier(["ssh-ed25519 garbage"])
            if failure == "malformed_key" else verifier_for(key)
        )
        verifier.verify_history(repo, head)


def test_missing_parent_is_not_a_complete_verified_history(scratch_dir):
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "alice")
    repo = publisher(scratch, key)
    parent = commit_file(repo, "a.txt", "a")
    head = commit_file(repo, "b.txt", "b")
    verifier = verifier_for(key)
    assert set(verifier.verify_history(repo, head)) == {parent, head}
    parent_object = repo.git_dir / "objects" / parent[:2] / parent[2:]
    saved = parent_object.read_bytes()
    parent_object.unlink()
    with pytest.raises(VerificationUnavailableError):
        verifier.verify_history(repo, head)
    parent_object.write_bytes(saved)
    assert set(verifier.verify_history(repo, head)) == {parent, head}


def test_good_verdict_from_another_key_set_is_not_accepted(scratch_dir, monkeypatch):
    scratch = pathlib.Path(scratch_dir)
    key = make_ssh_key(scratch, "signer")
    other = make_ssh_key(scratch, "other")
    repo = publisher(scratch, key)
    head = commit_file(repo, "a.txt", "a")
    original = repo.signature_report

    def report(rev, signers):
        # Simulate a backend whose trust configuration accepts another key.
        pathlib.Path(signers).write_text(f"signer@test {public_key_text(key)}\n")
        rows, diagnostics = original(rev, signers)
        assert rows[0].status == "G"
        return rows, diagnostics

    monkeypatch.setattr(repo, "signature_report", report)
    with pytest.raises(UnknownSignerError):
        verifier_for(other).verify_history(repo, head)
    assert verifier_for(key).verify_history(repo, head)[head]
