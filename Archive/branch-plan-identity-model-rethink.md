# Branch Plan: Identity Model Rethink

**Branch:** `identity-model-rethink`  
**Base:** `main`  
**Related docs:** `packages/wrasse-trust/README.md`,
`packages/wrasse-trust/README-brain-storming.md`,
`packages/wrasse-trust/device_provisioning_todo.md`,
`architecture.md`

## Context

The interrupted trust rethink has now converged on a simpler identity model
than the one currently described in parts of the repo and partially
implemented in code.

The old direction was a **layered** model:

- one rare-use per-team identity key for `Alice/{Team}`
- one or more per-team device keys certified by that identity key
- wrapped private identity-key material stored in `NoteToSelf`
- `device_binding` as the main device-admission cert

The new direction is a **device-only** model:

- there is no global "Alice ID" anywhere in the protocol
- there is no per-team private key sitting above device keys
- each team membership is represented by a fresh per-team participant UUID
- each enrolled device for that team has its own team-device key
- the equivalence class of those device keys is what "Alice/{Team}" means
- `membership` admits a per-team participant UUID and names its founding
  device key
- `device_link` expands that UUID's device set within a team
- `NoteToSelf` is socially special and useful for local bookkeeping, but not
  cryptographically privileged
- "admin" remains a social sync concept, not a cryptographic role

This branch is about making the docs honest and internally consistent before
planning the next implementation pass.

## Goal

After this branch:

1. the Wrasse Trust docs consistently describe the device-only, per-team
   identity model
2. the stable package README no longer presents the layered model as the
   current intended direction
3. the brainstorming README clearly captures the key design commitments,
   tradeoffs, and unresolved questions
4. the provisioning TODO explicitly warns readers that its wrapped-key flow is
   obsolete design debt, not the active direction
5. a future implementation branch can start from one coherent written model
   instead of reconstructing the intent from interrupted conversation logs

## Non-Goals

- refactoring code to remove layered-model structures
- preserving backward compatibility with pre-alpha trust data
- solving recovery UX or social-recovery mechanisms
- settling the exact `membership` claim schema beyond the minimum needed to
  discuss it clearly
- finalizing invitation or epoch mechanics

## Planned Changes

### 1. Reframe Wrasse Trust around device-rooted keys

Clarify in the brainstorming README that:

- the only private signing keys in the model are team-device keys
- per-team participant UUIDs are labels, not keys and not cross-team handles
- NoteToSelf is just another team cryptographically
- cross-team identity linking is optional and opt-in
- adding a new device is inherently per-team, even if the UI batches it

### 2. Remove cryptographic admin language from the core story

Make the docs explicit that:

- Small Sea's cryptographic layer records signed statements
- social sync behavior determines which signed statements teammates see
- "admin" names a social coordination pattern, not a privileged key class

### 3. Align certificate vocabulary with the new model

Document:

- `membership` as the cert that admits a per-team participant UUID and carries
  the founding device key
- `device_link` as the cert that expands an existing member's device set
- `device_binding` as a transitional leftover from the layered model that
  should be deleted in a later code refactor

### 4. Prevent further drift while code catches up

Update the stable README and provisioning TODO so a reader skimming the package
does not accidentally take the wrapped-key model as the latest intent.

## Validation

This branch should convince a skeptical reader that it improved repo integrity
if all of the following are true:

- the three Wrasse Trust docs tell one consistent story
- the story matches the recovered design conversation
- the story does not contradict the existing per-team UUID reality already in
  Manager schemas
- the docs plainly distinguish current code reality from current design
  direction
- open questions remain listed where the model is still genuinely unsettled,
  instead of being hand-waved away

## Outcome

Completed as a doc-convergence branch.

Implemented:

- a new branch plan capturing the recovered identity/trust direction
- a rewrite of the stable Wrasse Trust README so it distinguishes current code
  reality from current design direction
- a large update to the brainstorming README reflecting the device-only,
  per-team participant UUID model
- a prominent transitional warning on the provisioning TODO so it is no longer
  mistaken for the active design

Validation completed:

- reviewed the recovered session log in `~/tmp/recovered_session_2.txt`
- checked the updated docs against the current repo architecture and Manager's
  existing per-team UUID reality
- confirmed the Wrasse Trust doc set now tells one coherent story:
  current code is layered, current intended design is device-only

Not done on this branch:

- code refactors to remove per-team identity keys or wrapped-key flows
- schema changes for `membership` / `device_link`
- micro tests, since this branch only changed docs
