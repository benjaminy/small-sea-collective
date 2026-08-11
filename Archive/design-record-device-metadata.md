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

## Associated data must be constructible before decryption

`welcome_bundle_aad` binds `WelcomeBundle.version`, while the receiver currently supplies a separate
hardcoded `version=1` before it decrypts the bundle.
The problem is the duplicated literal and missing version validation, not that a protocol version is
ineligible for associated data.
The receiver independently knows which payload version it supports, so that expected version is valid
pre-decrypt context.

The general rule is narrower and reusable:
every associated-data input must be constructible before decryption from protocol constants,
receiver-held state, or cleartext envelope fields that the authenticated construction binds.
`{bootstrap_id, team_id}` in the linked-team bootstrap qualifies.
`joining_device_id_hex` qualifies because the joiner reads it from its own pending artifact.
An arbitrary value learned only by decrypting the ciphertext does not qualify.

Three version mechanisms in this flow have different jobs:

- `_WELCOME_BUNDLE_INFO` is the HKDF domain-separation label for the encryption construction.
- The cleartext envelope version selects the outer encryption-envelope format and is checked before decryption.
- The payload version selects `WelcomeBundle` serialization and semantics.
  In the current single-version implementation, both peers should use one shared expected-version constant
  in the AAD and validate the decrypted signed payload against that same constant.

If simultaneous payload versions are supported later, the outer envelope may need to carry a cleartext
payload-version selector and bind it into the authenticated construction.
That is a future negotiation design, not a reason to ignore versions now.

The AAD does not provide sender authentication.
The seal is anonymous public-key encryption over a public key published in the join request,
so anyone can produce a well-formed sealed bundle.
After fetch, the Ed25519 signature proves that the bundle was signed by a device key listed in the fetched
NoteToSelf database.
It does not by itself prove that the fetched database belongs to the participant the user intended to join,
because the remote descriptor is carried inside that same bundle.
Comparing the second confirmation string with the existing device's value supplies that intended-authorizer
binding when the bundle transport does not already provide equivalent authentication.
This is local ceremony evidence, not team standing or a globally trusted-installation state.
Without that evidence, the fetched identity is internally consistent but remains vulnerable to endpoint substitution.

## Version markers must be enforced

The bootstrap deserializers currently construct artifacts without checking their `version` fields.
That makes `JoinRequestArtifact.version` and `SignedWelcomeBundle.version` inert, while
`WelcomeBundle.version` is enforced only indirectly by the duplicated AAD literals.

The clean rule is to reject unsupported versions before acting on an artifact:
validate the join-request version before admission side effects, use the expected welcome-bundle version
in AAD before decryption, and validate all decrypted version fields before using the payload.
Bump only an artifact whose version is defined to cover the contract that changed.
Each nested artifact versions its own serialized contract:
`WelcomeBundle.version` covers the signed bundle fields, while `SignedWelcomeBundle.version` covers only the
outer wrapper fields.
A change confined to `WelcomeBundle` therefore does not bump `SignedWelcomeBundle`.
