"""Reproduce a stale fetch retaining a later, unverified-by-this-call pin."""

import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/cod-sync"))
sys.path.insert(0, str(ROOT / "packages/cod-sync/tests"))

from cod_sync.verify import SshCommitVerifier, UnknownSignerError
from cod_sync_test_helpers import (
    commit_file,
    make_cod_sync,
    make_repo,
    make_ssh_key,
    make_store,
    public_key_text,
)


PIN = "refs/peers/alice/main"


class RecordingVerifier:
    """Record the evidence returned by the real SSH verifier."""

    def __init__(self, allowed_keys):
        self.inner = SshCommitVerifier(allowed_keys)
        self.verified = None

    def verify_history(self, repo, head):
        self.verified = self.inner.verify_history(repo, head)
        return self.verified


def run():
    os.environ.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )
    os.environ.pop("SSH_AUTH_SOCK", None)

    with tempfile.TemporaryDirectory(prefix="retained-pin-") as directory:
        scratch = Path(directory)
        key_a = make_ssh_key(scratch, "a")
        key_b = make_ssh_key(scratch, "b")

        alice = make_repo(scratch / "alice", "alice")
        alice.configure_signing(key_a)
        old_head = commit_file(alice, "a.txt", "A\n", "signed A")
        old_store = make_store(scratch / "old-store")
        make_cod_sync(alice, old_store).publish()

        alice.configure_signing(key_b)
        later_head = commit_file(alice, "b.txt", "B\n", "signed B")
        new_store = make_store(scratch / "new-store")
        make_cod_sync(alice, new_store).publish()

        bob = make_repo(scratch / "bob", "bob")
        # Model an earlier transport observation: import B's objects and seed
        # the durable pin before the invocation under examination.
        make_cod_sync(bob, new_store).fetch()
        bob._run(["update-ref", PIN, later_head])

        verifier = RecordingVerifier([public_key_text(key_a)])
        result = make_cod_sync(bob, old_store, verifier).fetch(pin_to_ref=PIN)

        later_rejection = None
        try:
            SshCommitVerifier([public_key_text(key_a)]).verify_history(bob, later_head)
        except UnknownSignerError as exc:
            later_rejection = type(exc).__name__

        fresh = make_repo(scratch / "fresh", "fresh")
        fresh_verifier = RecordingVerifier([public_key_text(key_a)])
        fresh_result = make_cod_sync(fresh, old_store, fresh_verifier).fetch(
            pin_to_ref=PIN
        )

        output = {
            "prediction": {
                "fresh_pin_points_to_verified_A": True,
                "stale_fetch_observes_and_verifies_A": True,
                "stale_fetch_retains_descendant_B": True,
                "A_only_verifier_rejects_B": True,
            },
            "observed": {
                "fresh_pin_points_to_verified_A": (
                    fresh_result.pinned_head == old_head
                    and fresh.resolve_ref(PIN) == old_head
                    and set(fresh_verifier.verified) == {old_head}
                ),
                "stale_fetch_observes_and_verifies_A": (
                    result.observed_head == old_head
                    and set(verifier.verified) == {old_head}
                ),
                "stale_fetch_retains_descendant_B": (
                    result.pin_disposition == "stale"
                    and result.pinned_head == later_head
                    and bob.resolve_ref(PIN) == later_head
                ),
                "A_only_verifier_rejects_B": later_rejection == "UnknownSignerError",
                "pin_disposition": result.pin_disposition,
                "later_head_rejection": later_rejection,
            },
        }
        assert all(output["observed"][key] for key in output["prediction"]), output
        print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    run()
