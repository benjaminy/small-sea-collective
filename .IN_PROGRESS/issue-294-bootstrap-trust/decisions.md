# #294 decision sheet (draft; the owner decides)

Evidence: `transcripts.md` in this directory and the three strict xfails in `packages/small-sea-manager/tests/test_bootstrap_history_unverified.py`.
The post-bootstrap Core rule, implemented by `CoreHistoryVerifier`: every commit is signed by a certificate-validated device key, and every root commit is signed by the adopted anchor.
`CodSync(repo, store, verifier=...)` already runs a verifier on fetched history before adoption (`cod_sync/protocol.py:383`).

## Decision 1: Invitation acceptance

Evidence available before adopting history: the token's anchor and signed fields, plus certificates in the fetched `core.db`.
Those certificates chain the inviter's key to a genesis certificate whose subject key must equal the token's anchor.
That is enough to build a `CoreHistoryVerifier` before checkout.
Today `accept_invitation` checks out first and never verifies commits.

- A. Reorder: fetch, run the existing certificate and anchor checks, build the verifier, run `verify_history(repo, head)`, and only then check out.
  Also move the NoteToSelf `team` insert and key generation after verification, or undo them on failure.
- B. Keep the order and verify after checkout, deleting the directory on failure.
- C. Also pin the head to the proposal's `anchor_commit`.
  This needs the head to descend from it, which the owner has said not to rely on.

Recommendation: A. It reuses the verifier and satisfies the xfail (no directory, no team row).

## Decision 2: Linked-device bootstrap

Evidence: the sibling's signed response carries the anchor, and the signer key must already be a NoteToSelf team-device key.
The imported `core.db` supplies certificates, so the receiver can build the verifier before adopting anything.

- A. Inside the existing `try` that removes a failed clone, build `CoreHistoryVerifier` from the response anchor and the imported certificates, and verify `core_head`.
- B. Also sign a digest of the Core bundle in the response, so the bundle and not only its head is bound to the signature.
- C. Refuse cold start unless the receiver already holds the anchor from another source.

Recommendation: A. B is a cheap addition if you want it.
This path's signer trust bottoms out in NoteToSelf (Decision 4).

## Decision 3: Identity bootstrap

Evidence: the welcome signature and the fetched database.
The signer's public key is read from the fetched history, so the check is circular.
The confirmation strings do not cover the signer's key.

- A. Put the authorizing device's signing public key in the signed bundle and in the confirmation string.
  The receiver pins that key and, after fetching, checks history by the Decision 4 rule.
- B. Keep the bundle as is and apply the Decision 4 rule to the fetched history before `adopt_fetched_source`.
- C. Leave identity bootstrap to human comparison of the two strings.

Recommendation: A and B together.
The pinned key gives B a root to check against.

## Decision 4: NoteToSelf trust anchor

Evidence: none exists.
`user_device` rows are accepted if they appear in a structurally valid database.

- A. The identity founder's signing key is the anchor.
  The first device records it at creation, and each welcome bundle delivers it (Decision 3A).
  A commit is accepted if signed by a key in `user_device` at its parent commit, and the root by the anchor.
  This needs a new verifier that replays device rows commit by commit.
- B. Signed device certificates in `user_device`, chained to the founder key as Core's are, so `CoreHistoryVerifier`'s shape carries over.
- C. Human resolution: on an unrecognized signer, pause NoteToSelf sync and show the key to the user.

Recommendation: A first.
It is the smallest rule that closes the xfail, and B can come later.
Keep C as the failure behavior for either.
