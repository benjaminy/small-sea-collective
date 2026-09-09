# GitHub and documentation follow-up

No GitHub changes are part of this documentation revision.

- Update #190's title and scope: this branch delivers Cod Sync commit signing and an optional verifier with a verified acceptance boundary (scratch verification or the equivalent invariant in `plan.md`).
  State plainly that verification is wired only in tests until the berth-scoped keys and Manager wiring issue is completed.
  File and link the separate follow-up issues below when narrowing #190, before closing it.
  #190 closes on its library validation; it does not remain open across the follow-up implementations.
- File a separate follow-up issue: **Hub authenticated publication context**.
  This can proceed independently of #190.
  Bind writer, team, berth, and path on upload and download; note that `decrypt_group_payload` is not passed expected peer and path today, and that valid ciphertext must not be interchangeable across unrelated paths within one berth.
  Validate cross-path, cross-berth, and wrong-writer substitution rejection with passing controls across ordinary, passthrough, explicit proxy, bootstrap, and runtime artifact reads, or explicitly state each route's separate contract.
  Failed checks must not persist receiver crypto-state changes or become successful downloads through error translation.
- File a separate follow-up issue: **Berth-scoped keys and Manager wiring**.
  Depends on #190's signing and verification seam.
  Resolve the bootstrap trust source and the initial removal/unknown-authority policy through the corresponding issues before claiming runtime verification; the later reconsideration repair mechanism remains separate.
  Cover key provisioning and replacement, the Hub trust/signing interface (which berth a key belongs to, what evidence recognizes it, which local trust view was consulted, why an operation is blocked), reconciliation with the wrasse-trust README's device-only model, and passing a real verifier and signing key into Cod Sync.
  Promote the per-berth key rule and settled responsibilities to `architecture.md` and relevant specs when this issue is completed.
  Validate distinct keys across two berths, rejection of cross-berth keys, missing or ambiguous authority, trust-view changes during an operation, and actual Manager fetch/publication verification with passing controls.
  Revise Manager stored-history claims to match the demonstrated guarantees.
- File an issue for bootstrap trust: the transcript deliverable described in `notes.md`, including a removed original author and a substituted Core history.
- File an issue for removal and reconsideration policy: record the provisional direction and its limits, evidence retention, human decisions, and consistency with forward-only history.
  Do not imply acceptance is permanent or that removal policy is complete.
- File an issue: the Sender Keys ratchet is mismatched with repeatedly readable storage.
  Cite per-iteration replay key retention in `small_sea_hub/crypto.py` and the inability of newly admitted devices to derive earlier chain positions without additional retained key material.
- File an issue: provider rollback and equivocation detection.
- Retain existing link signatures; file a separate issue if a later proposal changes them.
- Reassess the #196 dependency against the named publication-context, wiring, and bootstrap issues once filed.
  #190 alone does not verify anything in the running system; do not treat closing it as satisfying #196's runtime trust prerequisites.
