"""Check that a stale fetch no longer returns a retained pin its verifier did not accept.

The original 2026-09-15 run reproduced that gap; #266 closed it.
See retained-pin.md for both results.
"""

import json
import os
from pathlib import Path
import sys
import tempfile

import anyio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/cod-sync"))
sys.path.insert(0, str(ROOT / "packages/cod-sync/tests"))

from cod_sync.async_verify import SshCommitVerifier as AsyncSshCommitVerifier
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
        self.inner = AsyncSshCommitVerifier(allowed_keys)
        self.verified = None

    async def verify_history(self, repo, head):
        self.verified = await self.inner.verify_history(repo, head)
        return self.verified


async def run():
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
        await make_cod_sync(alice, old_store).publish()

        alice.configure_signing(key_b)
        later_head = commit_file(alice, "b.txt", "B\n", "signed B")
        new_store = make_store(scratch / "new-store")
        await make_cod_sync(alice, new_store).publish()

        bob = make_repo(scratch / "bob", "bob")
        # Model an earlier transport observation: import B's objects and seed
        # the durable pin before the invocation under examination.
        await make_cod_sync(bob, new_store).fetch()
        bob._run(["update-ref", PIN, later_head])

        verifier = RecordingVerifier([public_key_text(key_a)])
        stale_rejection = None
        try:
            await make_cod_sync(bob, old_store, verifier).fetch(pin_to_ref=PIN)
        except UnknownSignerError as exc:
            stale_rejection = type(exc).__name__

        later_rejection = None
        try:
            SshCommitVerifier([public_key_text(key_a)]).verify_history(bob, later_head)
        except UnknownSignerError as exc:
            later_rejection = type(exc).__name__

        fresh = make_repo(scratch / "fresh", "fresh")
        fresh_verifier = RecordingVerifier([public_key_text(key_a)])
        fresh_result = await make_cod_sync(fresh, old_store, fresh_verifier).fetch(
            pin_to_ref=PIN
        )

        output = {
            "prediction": {
                "fresh_pin_points_to_verified_A": True,
                "stale_fetch_verifies_A": True,
                "stale_fetch_rejects_retained_B": True,
                "pin_still_points_to_B": True,
                "A_only_verifier_rejects_B": True,
            },
            "observed": {
                "fresh_pin_points_to_verified_A": (
                    fresh_result.pinned_head == old_head
                    and fresh.resolve_ref(PIN) == old_head
                    and set(fresh_verifier.verified) == {old_head}
                ),
                "stale_fetch_verifies_A": set(verifier.verified) == {old_head},
                "stale_fetch_rejects_retained_B": stale_rejection == "UnknownSignerError",
                "pin_still_points_to_B": bob.resolve_ref(PIN) == later_head,
                "A_only_verifier_rejects_B": later_rejection == "UnknownSignerError",
                "stale_fetch_rejection": stale_rejection,
                "later_head_rejection": later_rejection,
            },
        }
        assert all(output["observed"][key] for key in output["prediction"]), output
        print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    anyio.run(run)
