# Session signing and verification: design

Apps sign and verify their commits through their Hub session.
The framework never learns what an app's purposes mean.
This design follows the owner decisions in [prior-art.md](prior-art.md) and the findings in [framework-crypto-inventory.md](framework-crypto-inventory.md): keep it simple now, defer richer ideas.

## What an app can do

An approved session for app A on team T can:

1. **Sign a commit** on A's berth in T (`POST /session/sign`, exists today).
2. **Read the berth's signing public key** for this device (`GET /session/signing_key`, exists today).
3. **Verify commits** it fetched, getting an authority decision per commit (`POST /session/verify`, new).

There are no capability scopes inside an app.
Consent is the existing session PIN approval.
Encryption of uploads and downloads stays automatic, as it is today.

## Purpose labels

A purpose label is `<app name>/<label>`, for example `SmallSeaCollectiveFiles/content`.
The app chooses `<label>` (lowercase letters, digits and `-`).
The framework checks only that the prefix equals the session's app name, never what the label means.

The framework's own histories keep two unprefixed purposes instead: `note-to-self` for sessions on the NoteToSelf team, and `core` for Core sessions on any other team.
These are the only exceptions to the prefix rule.

This replaces the fixed `PURPOSES` sets in `cod_sync/work_context.py` and `berth_authority.py`, and the Files allowlist in `backend.sign_commit`.
Files uses `SmallSeaCollectiveFiles/content`, `/registry` and `/merge`, defined in Files' own code.

## Keys and delegations appear on first use

Before signing, an app asks for the berth's public key; Files does this when it builds its signer.
That request, and any signing request, calls one Manager operation, "ensure signing is set up": reuse this device's workhorse key for the berth or create it, then add a delegation from this device's team-device key if none exists yet, provided this teammate holds the berth under the adopted-anchor view.
An existing key without a delegation gets its delegation filled in.
Otherwise the Hub refuses with a typed reason (`berth_not_held` or `authority_anchor_absent`), and the app can show it.

The delegation covers the whole berth for this key, not a list of purposes.
Since there are no scopes inside an app, a purpose list in the delegation adds little: the berth already belongs to one app.
A verifier loses only the ability to enforce a grant narrower than the whole berth, which is exactly the intra-app scoping we are deferring.
This revises the #266 D4 decision, where a device delegates a list of purposes; record that on #266.
The work context still carries the purpose, so a receiving app can check that a commit's purpose fits where it found it.

Each device sets up its own key the first time it signs, so linked devices need no special step.

The Manager runs in-process with the Hub's backend today (`import small_sea_manager.provisioning`), so the Hub calling this operation keeps the decision in Manager code.
The operation must be safe when two callers run it at once, including the Manager's own CLI or web UI:
key creation must never overwrite an existing key (create the file exclusively), and the delegation insert plus its Core commit must happen under the Manager's existing local writer coordination.
Running it twice must change nothing the second time.

## Verification

`POST /session/verify` takes a list of raw commit objects (the bytes `git cat-file commit` prints, including any `gpgsig` header) and returns one decision per commit:

- `authorized`, with the signer's teammate and device and the delegation that authorized the key;
- or a refusal: `unsigned`, `bad_signature`, `wrong_scope`, `missing_authority`, `ambiguous_authority`, with the evaluator's reason.

The response also carries the identifier of the view it used.
The Hub evaluates the whole batch under one view, so an app can record which view justified accepting a head.
`authorized` means authorized under this device's current transitional view; it is not proof that the signer had authority when it signed.
The signer's recorded view is evidence only and is never compared against the verifier's view.

The Hub checks the SSH signature itself, reads the work context, and evaluates it with `berth_authority.evaluate` against this device's current view.
It checks that team and berth match the session and that the purpose carries the session's app prefix.
It returns decisions and changes nothing.
The app decides what to do with them, such as refusing to merge a parked head whose commits are not all authorized.

The Hub needs no access to the app's repository, and the app needs no Core access.
The Hub does not see trees or file contents; each signature covers its tree hash, and the app still checks content and merge results itself.
Exact team and berth checks stop a commit from another berth being replayed here; a replay within the same berth proves nothing about freshness or placement, so the receiving app checks placement using the purpose.

To start simply, an app checks the whole history reachable from a fetched head, through every merge parent, as the existing Cod Sync verifier does.
Sending only the commits added since the last accepted head is a later optimization.
Results are bound to commit IDs, and the app merges exactly the head it checked.

## Manager lists known apps

The Manager's app list shows every app name it has seen for each team, so the user can spot a lookalike name.
This is display only.

## Session deletion

The user can delete a session in the Manager.
The Hub then refuses it like an unknown session.
Revoking delegations stays deferred (task 35).

## Deferred

- Scopes per feature or per client, confirmation on each use, grants remembered beyond sessions.
- Proving which program is behind an app name.
- Delegation revocation and what happens to old signed work.
- Signing Cod Sync publication links through the Hub (task 30 decides whether links need it).
- Encryption operations apps call explicitly.

## Changes, in order

1. Purpose labels: app-prefixed format in `work_context.py`; drop fixed app purposes there and in `berth_authority.py`; Hub checks the prefix; Files defines its own labels.
2. Delegations cover a berth, not a purpose list (schema change; bump the version).
3. First-use key and delegation setup via the Manager, with typed refusals.
4. `POST /session/verify` and client method.
5. Files verifies before it adopts or merges a fetched head (today it fetches with no verifier and merges; `files.py` `_cod_pull`, `_cod_merge_ref`).
6. Session deletion in the Manager.
7. Manager app list.

Items 1 to 3 replace task 37.
Items 4 and 5 overlap tasks 30 and 32 and should be reconciled with them.
Items 6 and 7 are small and independent.
