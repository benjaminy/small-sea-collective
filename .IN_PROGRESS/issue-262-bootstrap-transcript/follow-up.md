# Planned issue updates after the design work

No GitHub changes are part of this planning kickoff.

- [#262](https://github.com/benjaminy/small-sea-collective/issues/262): link the durable transcript and its adversarial walkthroughs once complete.
  Report what remains unimplemented separately from what the design establishes.
- [#266](https://github.com/benjaminy/small-sea-collective/issues/266): carry forward the settled initial trust source, required authority evidence, berth/purpose binding, local-view identity, and pause reasons for runtime wiring.
  Keep key generation/derivation and the Hub interface in that issue's scope.
- [#263](https://github.com/benjaminy/small-sea-collective/issues/263): carry forward the newcomer/removed-author case, distinguishing an inviter's prior acceptance from the newcomer's acceptance and from future authority.
  Its preservation-and-pause direction remains provisional; do not claim permanent acceptance or a completed reconsideration policy.

If the audit finds a concrete enforcement gap that those issues do not cover, draft a focused follow-up with the failing scenario, evidence, and affected boundary.
Do not expand this design branch into runtime repair or duplicate existing issue scope.

## Enforcement gaps found during the field classification

- **Stored prekey bundles are not bound to the device key they are filed under.**
   `redistribute_sender_key` picks a device key id from the cert graph, loads the `device_prekey_bundle` row filed under that id, and calls `x3dh_send`.
   X3DH verifies the signed prekey against the `identity_signing_public_key` carried in the same bundle, and nothing checks that key against the trusted device public key.
   Anyone who can write the team database can redirect a redistributed sender key to a key of their choosing.
   The other `x3dh_send` caller, `create_linked_device_bootstrap`, takes its bundle from a signature-checked join request instead, so the gap is specific to the stored row.
   This is separate from #262's bootstrap question and is not covered by #266's berth scoping; it needs its own issue.
- **No Manager path enables Git commit signing.**
   `Repo.configure_signing` is called only from cod-sync's own tests, so Manager commits are unsigned.
   Confirm with a human whether that is deliberate staging for #190 and #266 before filing anything.
