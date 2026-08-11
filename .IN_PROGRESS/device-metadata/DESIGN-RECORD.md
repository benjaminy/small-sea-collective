# Design Record: device metadata placement

The permanent docs state the rulings.
This record keeps the alternatives that were rejected and the reasoning that would otherwise be lost.

## Device labels are participant-tier, and the rejected alternative was a team-tier column

The obvious implementation of "let me name my devices" is a `label TEXT` column on `team_device`,
which is where teammates already look devices up.
That was rejected.

A durable synced row is effectively unerasable: it lands in every teammate's clone and in team Git history.
Device labels are also an unusually rich leak for their size — hardware, OS, employer, device count,
and travel patterns are all routinely encoded in what people name their laptops.
Unerasable plus high-PII is the worst available combination, and nothing in the trust model needs it.
Trust flows through `key_certificate` traversal and teammates address devices by `device_key_id`;
multi-device UI can say "2 devices" without naming them.

If a teammate-visible label is ever wanted, it must reuse the established shape rather than reinvent it:
`admission_proposal.invitee_label_commitment` (signed) alongside `invitee_label_payload`
(separable, unsigned, droppable).
A plain `TEXT` column bypasses that pattern, which is the specific mistake to avoid.

The condition for revisiting is a concrete UI need that `device_key_id` plus a device count cannot serve.

## Last-seen was rejected from the synced tier on convergence grounds, not privacy

Privacy is the easier argument, but the structural one is stronger and less likely to be re-litigated:
last-seen is not a fact about the team at all.
It is an observation by one device, and every device sees a different last-seen for the same peer.
There is nothing to converge on, so a synced row is last-writer-wins noise rather than shared state.
It also fails the churn test independently — a row rewritten on every sync means a commit on every sync,
and the accumulated history is a durable per-device activity timeline nobody asked for.

The precedent to copy is `unknown_app_sighting(first_seen_at, last_seen_at, seen_count)` in the Hub-local schema.

## Associated data may only bind values the receiver already holds

`welcome_bundle_aad` bound `WelcomeBundle.version`, which exists only inside the sealed plaintext.
The receiver needs the AAD before it can decrypt, so it cannot read the version it is required to bind,
and the joiner hardcoded `version=1` as the only thing it could do.
That literal silently converts any future version bump into a total decryption failure.

The rule that resolves it, and that the other AAD sites in the repo already follow:
associated data may only contain values the receiver holds independently of the ciphertext.
`{bootstrap_id, team_id}` in the linked-team bootstrap qualifies.
`joining_device_id_hex` qualifies — the joiner reads it from its own pending artifact.
A field that only exists inside the ciphertext never qualifies.

Removing the version from the AAD costs nothing that was real.
The AAD was not providing sender authentication: the seal is anonymous public-key encryption over a public key
published in the join request, so anyone can produce a well-formed sealed bundle.
Sender authentication comes from the Ed25519 signature over the bundle plaintext,
which already covers the version, and which cannot be checked until `finalize_identity_bootstrap`
because the signer's public key lives in the NoteToSelf DB fetched between the two steps.
Pre-decrypt version checking is separately handled by the cleartext envelope version,
which is what that field is for.

## Version bumps are kept even though nothing validates them

Both bootstrap deserializers are bare `Artifact(**payload)` and no code reads a `version` field.
The bumps on `JoinRequestArtifact`, `WelcomeBundle`, and `SignedWelcomeBundle` are therefore inert today.
They are kept as deliberate practice at maintaining version markers, per the AGENTS.md rule about
keeping schema/version markers in place so future compatibility work stays possible.

This is only defensible once the AAD no longer depends on the payload version.
Bumping a version that silently breaks decryption is worse than not versioning at all,
because it trains the habit and hides the cost.
