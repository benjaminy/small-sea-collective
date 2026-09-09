# Notes

## 2026-09-09: revised design assessment

The working direction is commit signatures with existing link signatures retained.
Sequencing is settled in `plan.md`; bootstrap trust remains a follow-up.

### Evidence from the current implementation

- `packages/cuttlefish/cuttlefish/group.py` signs IV plus ciphertext with a sender-chain signing key.
  The envelope's sender device key ID, chain ID, and iteration are outside that signature; AES-GCM associated data is the team ID.
  This is not yet authenticated berth/path context, and attribution to an expected principal depends on authenticated sender-key distribution.
- `packages/small-sea-hub/small_sea_hub/crypto.py` looks up peer sender state by team and payload device key ID.
  Ordinary downloads in `backend.py` do not pass expected peer and path into `decrypt_group_payload`.
  The prior plan's statement that the Hub already binds chains to senders was incorrect.
- Transport signatures attest the re-encrypting sender rather than preserving the original publisher's signature.
  Original signed Git commit objects survive a rollup if their objects are retained.
  A separately signed link could preserve a publication claim too; these are different units of evidence.
- `packages/cod-sync/cod_sync/protocol.py` skips `_import` when `_already_satisfied` sees an existing head and prerequisites, and chain traversal stops at an existing predecessor.
  `unbundle` writes objects before a proposed signature check in `_import` could run.
  Leaving refs unchanged on failure is insufficient if those objects allow a retry to skip verification.
- `packages/cod-sync/cod_sync/repo.py` exposes `commit_paths` and `merge` as well as `commit` and `commit_tree`.
  The signing audit must cover all relevant creation paths.
- `packages/small-sea-manager/spec.md` describes transcript-bound device linking and linked-team bootstrap with a fresh team-device keypair.
  These are starting evidence for the bootstrap audit, not proof of the proposed per-berth key model.

### Directions from the user discussion

A physical device should have a distinct key for each berth.
Record this as the intended model rather than quietly building new signing on the existing team-device key.
A cross-berth statement about the same physical device must not imply one cryptographic principal.
This improves scope isolation, provided key selection and recognized authority enforce that scope.
It does not remove within-berth context substitution, nor promise that certified keys cannot be correlated to one physical device.
Generation versus derivation, key-purpose separation, and the relationship to bootstrap identity remain open.

The Hub can enforce its upload/download slice and expose useful trust information.
Apps remain responsible for important verification and semantic decisions; a client library cannot force arbitrary apps to perform them.
This revises the earlier requirement that ordinary clients must not be able to skip commit verification.
Cod Sync should enforce its own supported acceptance contract, while Hub guarantees remain true independently of whether an app uses Cod Sync.

A Hub API for discussing keys and trust is a plausible need, not yet an approved endpoint design.
Useful questions include which berth a key belongs to, what evidence recognizes it, which local trust view was consulted, and why an operation is blocked.
Separate evidence and local framework policy from app-specific integration decisions and Manager-owned management authority.
Avoid reducing all of these answers to one boolean or an allowed-signers file.

Preserving accepted history on removal while pausing unresolved new history is a provisional starting point.
The user explicitly expects richer policy, potentially reconsidering updates previously accepted from a device later found malicious.
Preserve evidence and avoid promises of permanent acceptance; defer the reconsideration and repair mechanism.
Any future repair design must address the repository's forward-only history rule rather than silently resetting shared history.

## Bootstrap prior art: a focused comparison

These sources inform the next design pass; they do not constitute a protocol audit or a decision to adopt their systems.

- [MLS, RFC 9420 sections 3, 5.3.1, and 12.4.3.1](https://www.rfc-editor.org/rfc/rfc9420.html#section-5.3.1) separates credential authentication from delivery and requires credential validation when joining through GroupInfo.
  Welcome provides a specified joining flow, but identity authentication remains an explicit dependency.
  Small Sea inference: specify both authenticated initial evidence and the subsequent history checks; encrypted onboarding alone cannot establish identity.
  Mismatch: MLS group epochs and messaging do not supply Small Sea's durable Git-history authorization policy.
- [TUF specification, root update](https://theupdateframework.github.io/specification/latest/#update-root) starts from trusted root metadata and verifies successive root changes against old and new authorization thresholds.
  The [TUF FAQ](https://theupdateframework.io/docs/faq/) explicitly leaves initial Root delivery to the adopter.
  Small Sea inference: distinguish initial anchor delivery from verifiable evolution of authority.
  Mismatch: a software repository's root roles and freshness rules do not define decentralized team membership or offline history acceptance.
- [Git SSH trust configuration](https://git-scm.com/docs/git-config#Documentation/git-config.txt-gpgsshallowedSignersFile) makes allowed-signers membership an input to trusted signature verification.
  Small Sea inference: use this machinery for the chosen cryptographic check while keeping historical authority and integration policy explicit.
  Key lifetime configuration must not be treated as independent evidence of when a potentially malicious signer acted.

Next research deliverable: one Small Sea bootstrap transcript from independently authenticated invitation or sibling-device evidence through Core history to berth-scoped keys.
Show where each authority decision comes from, what the inviter is trusted to assert, and where missing evidence pauses progress.
Include an original author removed before the newcomer joins and a substituted Core history listing the attacker's keys.
The comfort from prior art is in explicit anchors and validated transitions, not in assuming a signed snapshot authenticates itself.

## 2026-09-09: sequencing and the verifier seam

The six open decisions were sequenced into a narrowed #190 and separately tracked follow-up issues; see `plan.md`.
This branch keeps every change inside `packages/cod-sync`.

The verifier on `CodSync` is optional.
The alternative was a required parameter with the Manager passing an interim policy built from the existing recognized device table.
That would have signed commits with the team-device key the plan says not to use long-term, and spread the branch across four Manager modules.
The cost of the chosen option is that the merged state verifies nothing in the running system until the berth-scoped keys and Manager wiring follow-up issue is completed.
That is acceptable because the framework already cannot force apps to verify, and the seam plus tests are the evidence this branch is for.

## 2026-09-09: signing spike results

A throwaway spike ran the real `cod_sync.repo.Repo` against git 2.50.1 (Apple Git-155) with fresh Ed25519 SSH keys, no ssh-agent, and `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at `/dev/null`.
The script is not part of the branch.
That last isolation step mattered: on the first run the developer's own `~/.gitconfig` openpgp signing silently signed the "unsigned commit" control, so it verified when it should have failed.
Micro tests for step 1 must isolate git config the same way, or they will pass for the wrong reason on a developer machine and fail in CI.

### Signing works agent-free, but `commit_tree` is the exception

`user.signingkey` set to the path of an SSH private key signs without ssh-agent, so tests and headless callers need no agent.
With `gpg.format=ssh` and `commit.gpgsign=true` set on the repo, `commit`, `commit_paths`, and `merge` all produce signed commits that `git verify-commit` accepts.
`commit_tree` produces an unsigned commit: `git commit-tree` is plumbing and ignores `commit.gpgsign` entirely.
It signs only when passed `-S` explicitly, which works and verifies.
This is not hypothetical for the framework: `small_sea_manager/note_to_self_sync.py:596` builds its integration commit through `commit_tree`, so that production path would silently stay unsigned if step 1 only sets config.

### Bundle evidence and corrected inferences

`create_bundle_from_head` (which clones `--shared --bare` into a temporary snapshot) and `import_bundle` preserved the original commit signatures in a fresh destination.
The second spike below corrects the first report's claims about verdict classification, batching cost, squash evidence, and mandatory merge abort.
The original report and critique remain in Git history (`9f3c348` through `e9c11b8`).

## 2026-09-09: reconciliation after the critique

A second throwaway spike ran the experiments the critique named as missing.
It used the same Git version and isolation as the first spike.
Neither script nor raw outputs are committed; these are reported observations to convert into micro tests.

### The unsigned control is genuinely unsigned under isolation

Under the isolation above, a repo with no signing config produces `%G?` = `N`.
This confirms the isolation recipe the spike report prescribed, so step 1's micro tests can assert the negative control rather than assume it.

### Git verdicts do not fully classify verification failures

[Git documents eight `%G?` statuses](https://git-scm.com/docs/pretty-formats): `G B U X Y R E N`.
The reported SSH results do not reliably separate inability to check from unsigned commits or unknown signers:

| verification config | `%G?` | exit | stderr |
| --- | --- | --- | --- |
| correct allowed-signers file | `G` | 0 | empty |
| `gpg.ssh.allowedSignersFile` unset | `N` | 0 | `error: gpg.ssh.allowedSignersFile needs to be configured and exist` |
| file configured but absent | `U` | 0 | empty |
| file present but empty | `U` | 0 | empty |
| file naming a different key | `U` | 0 | empty |
| tampered commit object | `B` | 0 | empty |

`E` did not appear in any of these cases.
The dangerous one is an unset allowed-signers file: a correctly signed commit reports `N`, the same verdict as a genuinely unsigned commit, and `git log` still exits 0 with the diagnostic only on stderr.
So the verifier cannot recover "could not check" from the verdict.
The verifier should own the allowed-signers file and effective verification configuration, accept only `G`, and reject unknown statuses.
File existence alone does not establish usable configuration; classification of tool/configuration failures still needs executable evidence.
`%GF` reports the signing key's fingerprint (`SHA256:...`) alongside the verdict, so evidence can identify the key without treating Git author text or the allowed-signers principal as authenticated human identity.

### The sweep is about twice as fast, not far cheaper

Over 50 signed commits, best of three runs: one `git log --format='%H %G?'` sweep took 644 ms (12.9 ms per commit) against 1275 ms for 50 `git verify-commit` calls (25.5 ms per commit).
This measures roughly a twofold improvement for this workload, but does not isolate signature-check cost from process startup or other overhead.
Batching stays an implementation convenience and is not a prerequisite for correctness.

### The merge-abort inference is withdrawn

With signing configured, the key file missing, and signing left required throughout:

- `Repo.merge` raises `RepoError`, HEAD does not move, `MERGE_HEAD` is present, and the merge is staged with no conflicts.
- A later `Repo.commit` on that repository also raises, creates no commit, moves no ref, and leaves `MERGE_HEAD` in place.
- After restoring the key, the same `Repo.commit` completes a genuine two-parent merge commit that verifies `G`.

Git does not fall back to an unsigned commit while signing remains required.
The unsigned merge commit in the first spike required explicitly switching signing off, which is a caller's decision rather than a git fallback.
This case supports leaving the merge in progress for retry, without adding automatic abort behavior.
[git-merge](https://git-scm.com/docs/git-merge) also warns that abort cannot always reconstruct pre-existing uncommitted changes.
Any cleanup behavior stays a separate API decision.

### Verifying the parents says nothing about the merge result

A publisher merged Alice's and Bob's signed histories, edited `alice.txt` inside the merge tree, and signed the merge with an untrusted key.
Alice's and Bob's commits still verified `G` against their own keys, and the merge verified `U`; with the publisher's key added to the allowed signers, the merge verified `G`.
The merge tree holds `alice work\nSNEAKED IN BY PUBLISHER` where Alice signed only `alice work`.
So a set of `G` parents is not evidence about the tree an accepted head resolves to, and this is direct support for the plan's all-required-commits invariant: the `U` merge must block acceptance even though both parents verify.
Separately, an isolated commit verifying without its tree or parents present remains only a statement about that commit object.
Graph and bundle validation stay separate checks, and an incomplete history must not be accepted because the commits that happen to be present verify.

### A signed squash is not evidence-free

A `commit-tree -S` squash over the merged tree, with no parents, verified `G` under the publisher's key.
The original commit objects were absent, so the original authors' signatures were gone.
The correct statement is that a squash loses original authorship evidence while retaining the squash signer's attestation of the published tree, not that it is evidence-free.

### The compatibility claim stays unvalidated

Only git 2.50.1 was exercised, here and in the first spike.
The earlier Git 2.34+ claim was based on SSH signing availability, not testing.
Record the tested version; broader compatibility remains unvalidated.

### Link signatures

No new evidence bears on step 4.
Keeping `extensions.signatures`, `signed_link`, and `verify_link_signature` unchanged remains the recommendation, because `canonical_link_bytes` covers `link_id`, `head`, `bundle_id`, and `previous`: a publication claim and chain position that commit signatures do not attest.
Neither prevents a signer from re-signing an old head or a provider from replaying an old signed link, so freshness stays in follow-up work.

### Experiments to convert into micro tests

Isolated unsigned control; each verification-config row in the table above; a tampered commit object; merge signing failure, failed retry, and success after the key returns, each with a passing control; the publisher merge with an extra change against both allowed-signer sets; the signed squash.

One incidental find, unrelated to signing: `Repo.checkout_branch(branch)` with no start point runs `checkout -B branch`, which resets the branch to HEAD.
Both spikes silently produced non-diverging branches until start points were passed explicitly.
The behavior matches the docstring; tests that build merge histories need to pass start points.

## Review of `e9c11b8`: next evidence

The merge retry, unsigned control, altered merge tree, and signed squash address the earlier critique, but remain unrepeatable from this commit alone.
Convert them into retained micro tests before another throwaway spike.
Assert fixture topology as well as signature state: explicit branch start points, divergent tips, and the expected merge parents.

Finish the verifier error contract under controlled SSH configuration.
An intentionally empty key set or a different recognized key means no signer is recognized; a missing generated file or failed verifier tool means verification could not run reliably.
Do not infer those causes from `U` alone or label ambiguous failures as proven tampering.

The decisive next test is rejection and retry through both `fetch` and publication observation, including already-present heads, existing predecessors, merge-side ancestry, and failure after earlier chain imports.
Assert unchanged refs and pins, no publication upload after failed initial observation, and a passing control.
Individual signature checks do not yet demonstrate this acceptance boundary or complete graph validation.
