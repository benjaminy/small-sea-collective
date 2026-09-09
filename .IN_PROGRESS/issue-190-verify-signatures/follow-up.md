# GitHub and documentation follow-up

Acted on 2026-09-09.
All issue filing and linking below is done; the only remaining item is closing #190, which is left to the human handling the merge.

- **Done.** #190 retitled "Sign Cod Sync commits and enforce a verified acceptance boundary" and rewritten to the narrowed library scope: commit signing, the verified acceptance boundary, the explicit key set, unchanged link signatures, and the validation actually performed.
  It states plainly that verification is wired only in tests until #266, and that it closes on its library validation rather than staying open across the follow-ups.
  All six follow-up issues are linked from it.
- **Not done, for a human.** Close #190 once the branch merges.
  It is still open because the work is not merged.
- **Done.** #261 — Bind authenticated publication context at the Hub upload/download boundary.
  Independent of #190.
  Records that `decrypt_group_payload` is not passed expected peer and path, and requires cross-path, cross-berth, and wrong-writer rejection with passing controls across all five read routes or an explicit per-route contract.
- **Done.** #266 — Berth-scoped signing keys and Manager verifier wiring.
  Depends on #190's seam and on #262 and #263.
  Covers key provisioning and replacement, the Hub trust/signing interface, reconciliation with the `wrasse-trust` README's device-only model, passing a real verifier and signing key into Cod Sync, and the documentation promotion and Manager stored-history claims due on completion.
- **Done.** #262 — Write the Small Sea bootstrap trust transcript.
  Includes the removed original author and substituted Core history cases, and the prior-art comparison from `notes.md` so it survives this branch folder.
- **Done.** #263 — Define removal and reconsideration policy for signed history.
  Records the provisional direction and its limits, evidence retention, human decisions, and the forward-only history constraint, without implying acceptance is permanent.
- **Done.** #264 — Sender Keys ratchet is mismatched with repeatedly readable storage.
  Cites the per-iteration replay key retention at `small_sea_hub/crypto.py:78-80` and `:105-107`.
- **Done.** #265 — Detect provider rollback and equivocation.
  Cross-referenced to #198 and #199.
- **Done.** Existing link signatures retained; #190 says any later change to them is tracked separately.
- **Done.** The #196 dependency was reassessed in a comment there: its runtime trust prerequisite is #266, not #190, and #261 also bears on it.
