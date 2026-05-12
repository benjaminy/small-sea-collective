# Issue 130 Plan: First App-Owned Materialization Consumer Check

**Branch:** `codex-issue-130-vault-materialization-check`
**Issue:** #130, "Add first app-owned materialization consumer integration check"
**Base:** `main`
**Status:** planning

## Purpose

Issue #116 removed the mistaken `NoteToSelf/{AppName}` directory stubs and
documented the ownership boundary.
Small Sea provisions app access.
Each app owns its own local participant/team materialization.

Issue #130 should make that rule executable for the first real app consumer.
Shared File Vault is the right canary because it is a normal app, already uses
Hub sessions as `SharedFileVault`, and already has local materialized state.

The branch should prove that Vault can derive app-owned local storage
coordinates from the public Hub session metadata available today:
`participant_hex` and `berth_id`.
Inside Vault, that Hub `berth_id` is translated into a Vault `team_id`,
because Vault's app context is already fixed.
Friendly names such as `team_name` and `app_name` remain display and identity
labels.
Before a session exists, `team_name` may still be a user-facing selection key
because the current Hub request API selects teams by friendly name.
They must not be used as Small Sea-derived path components for Vault's durable
local team storage.

Manager participates as the provisioning authority.
It should keep registering and activating Vault without creating Vault's app
data tree.
Vault should consume the resulting Hub session metadata without reading
Manager/Core databases directly.

## Branch Contract

This branch is successful if a reviewer can see all of the following in code
and micro tests:

1. Vault has one small, explicit app-owned materialization helper or context
   boundary that derives local coordinates from `participant_hex` and
   `berth_id` returned by `GET /session/info`, then exposes that coordinate
   inside Vault as `team_id`.
   When Vault resumes a cached session from `team_sessions[team_name]`,
   resumed `session_info` is the source of truth for `team_id`; Vault must not
   infer materialization coordinates from the cache key.
2. Vault's session-backed local team storage uses a stable opaque `team_id`
   sourced from Hub `berth_id`.
   The friendly team name is available for display and user-facing commands,
   but is not the durable directory or SQLite key that separates Vault teams.
3. Vault team contexts remain hard-separated.
   One participant with two distinct team IDs cannot converge Vault state just
   because both contexts carry the same friendly team name.
   Participant contexts also remain separated by `participant_hex`.
4. Manager registration and activation still only write Core registration
   state.
   They do not create `NoteToSelf/SharedFileVault`, `Team/SharedFileVault`, or
   any other Vault-owned working tree.
5. Vault does not read `SmallSeaCollectiveCore` databases directly.
   Either no public metadata gap is found, or the gap is recorded against #8
   rather than patched with an app-side DB read or a new ad hoc endpoint.

## Non-Goals

- Do not implement the Hub read-only self-configuration API from #8.
- Do not implement app unification, same-app race convergence, or berth storage
  indirection from #113, #114, or #115.
- Do not implement sync-side materialization opt-out from #117.
- Do not make Manager a generic app-home allocator.
- Do not standardize a framework-wide app-home layout.
  Any new path names introduced here are Vault-owned.
- Do not preserve old local Vault directory compatibility.
  The repo is pre-alpha; prefer the clean shape.

## Proposed Vault Layout

The exact names are Vault-owned, not framework-owned.
The working proposal is:

```text
{vault_root}/
  participants/
    {participant_hex}/
      checkouts.db
      teams/
        {team_id}/
          registry/
            git/
            checkout/
            codsync-bundle-tmp/
          niches/
            {niche_name}/
              git/
              codsync-bundle-tmp/
```

The important contract is that the participant coordinate is
`participant_hex`, and the Vault team coordinate is `team_id`.
That Vault `team_id` is sourced from Hub `session_info["berth_id"]`.

The friendly `team_name` may still appear in user-visible text, route
parameters, command arguments, and pre-session selection flows where the
current Hub APIs still require it.
It should not be the local directory or SQLite key that separates Vault's
durable team state.

The absence of `checkout/` under `niches/{niche_name}/` is intentional.
Niche checkouts are user-chosen directories tracked in Vault-local SQLite state,
while the registry has a private checkout because the registry itself is a
Vault-managed work tree.

## Coordinate Scope Decisions

Issue #130 is about durable app-owned materialization, not every string that
passes through Vault.
The branch should make each current `team_name` use explicit:

| Surface | Decision for this branch | Why |
| --- | --- | --- |
| Local directories under `vault_root` | In scope: use Vault `team_id` for the team coordinate. | This is the direct issue #130 path-coordinate check. The value comes from Hub `berth_id`. |
| `checkouts.db` checkout rows | In scope: use `team_id` in uniqueness and lookup keys. | The database is local Vault materialization; using `team_name` there would leave the boundary half-converted. |
| `checkouts.db` peer sync rows | In scope: use `team_id` in the primary key. | Peer watermarks and parked refs are Vault-team-scoped local state. |
| Config `team_sessions[...]` | Out of scope unless implementation proves it blocks the branch. | This cache is keyed before a session exists, and the public session request API currently selects teams by friendly name. Richer pre-session selection belongs with #8. After login, code must validate `session_info` and use the resulting context for materialization. |
| Config `peer_signal_watermarks[...]` | In scope: default to moving it into Vault-local SQLite beside peer sync state. | This is local sync state observed through a team session, and the watermark belongs with the peer-sync row it watermarks. If implementation keeps it in config for a narrow reason, it must still key by `team_id`, not `team_name`. |
| Cloud object prefixes such as `vault/{team_name}/registry/` | Conditionally in scope: remove the friendly team-name coordinate only if Hub already berth-scopes cloud operations. | Cloud operations should happen inside a Hub berth/session. If the Hub already provides the opaque berth boundary, Vault object keys inside that boundary should be Vault-internal paths, not team coordinates. If not, this rolls into follow-up. |

## Cloud Prefix Decision

The current cloud prefixes include the friendly team name:

```text
vault/{team_name}/registry/
vault/{team_name}/niches/{niche_name}/
```

That should change on this branch if the Hub sanity check passes.
All Small Sea cloud operations should happen in the context of a berth/session.
If a cloud backend needs a bucket, prefix, or other storage boundary to
implement that context, that boundary belongs at the Hub/berth layer and should
come from opaque berth/session metadata.

Within a Hub-scoped Vault session, Vault does not need an additional team
coordinate in object keys.
Use Vault-internal paths only:

```text
registry/
niches/{niche_name}/
```

If an app-level namespace marker is still useful for readability or future
internal subdivision, `vault/registry/` and `vault/niches/{niche_name}/` are
acceptable.
In that case, `vault/` is a Vault-owned literal namespace, not a value derived
from `session_info["app_name"]`.

There are only two outcomes:

1. Hub cloud operations are already berth-scoped by opaque session metadata.
   Remove `team_name` from Vault object prefixes and do not add `berth_id`.
2. Hub cloud operations are not berth-scoped yet.
   Leave Vault cloud prefixes unchanged on this branch, ship the local
   directory and SQLite-key conversion, record the cloud-scoping gap in
   `FOLLOW-UP.md`, and route the broader fix to #8, #114, or #115 as
   appropriate.

Forecast:
because berth storage indirection is explicitly out of scope for this branch,
it is plausible that the sanity check will find a Hub/cloud scoping gap.
Reviewers should not be surprised if #130 lands the local materialization check
while leaving cloud prefixes unchanged behind a documented follow-up.

Pre-alpha cloud state created under old prefixes may be abandoned if the cloud
prefix change does land.
This branch should not add cloud migration or compatibility shims.

If implementation shows that session-backed Vault operations cannot be
converted without a missing metadata field, stop and record the gap against #8.
Do not add a Vault-only Hub endpoint or direct Core DB read.

## Likely Code Shape

Define the small Vault-owned context type in
`packages/shared-file-vault/shared_file_vault/vault.py`, where the path helpers
live.
Construct it in `packages/shared-file-vault/shared_file_vault/sync.py`, where
Hub sessions are opened or resumed.

The context should be constructible from Hub session info:

```python
VaultMaterializationContext(
    participant_hex=session_info["participant_hex"],
    team_id=session_info["berth_id"],
    team_name=session_info["team_name"],  # display/selection label only
    app_name=session_info["app_name"],
)
```

Then update Vault path helpers so session-backed operations can pass the
opaque Vault team coordinate independently from the friendly team name.
Commit to the context object rather than adding a threaded optional
compatibility parameter.

Because this is pre-alpha, avoid migration shims.
If tests or callers need updates, update them directly.

## Red Micro Tests First

Write the main tests before implementation.
They should fail for the current code because Vault local paths currently use
`team_name`.

### 1. Vault derives coordinates from session metadata

Location: `packages/shared-file-vault/tests/test_hub_sync.py` or a new focused
test file.

Set up a participant, register and activate `SharedFileVault`, open a Hub
session, and call the new Vault materialization entry point.

Assert:

- `session_info` includes `participant_hex` and `berth_id`.
- the created Vault participant path includes `participant_hex`;
- the created Vault team path includes `team_id`, sourced from `berth_id`;
- the created path does not include the friendly `team_name`;
- `app_name == "SharedFileVault"` is checked but not used as a local Small
  Sea-derived directory coordinate.

### 1b. Vault cloud prefixes are within-session paths

Only add or enable this assertion if Phase 2b runs.
It belongs with the Hub scoping sanity check rather than the local coordinate
test and is not part of the Phase 1 red-test gate.

Assert:

- Hub-backed Vault remotes use within-session object prefixes that do not include
  `team_name`;
- any `vault/` object prefix is a literal Vault namespace, not derived from
  `session_info["app_name"]`.

### 2. Same friendly name does not collapse Vault team contexts

Create one participant with two Vault materialization contexts that have the
same friendly `team_name` and different `team_id` values.
If Manager cannot currently create two same-name teams through public helpers,
exercise the Vault context/path layer directly and record that the broader
cross-org duplicate-team setup belongs with #8 or app/team identity follow-up
work.
When run synthetically, this test proves only the Vault path/helper contract.
It is not a full-system duplicate-team integration proof.
That fuller proof depends on real duplicate-team or duplicate-friendly-name
setup work and belongs with #113, #115, or related identity follow-up.

Assert:

- the two contexts produce distinct registry repo paths;
- the two contexts produce distinct niche repo paths for the same niche name;
- checkout and peer-sync rows for the same niche do not collide;
- a matching friendly team name does not cause path convergence.

### 2b. Participant contexts remain hard-separated

Keep a smaller participant-separation check as a regression guard.
It is not the main proof for team IDs, because the participant layout already
handles the easy cross-participant case.

### 3. Manager provisions access but not Vault storage

Use Manager provisioning for participant registration and team activation.
This test should point to the existing #116 coverage for the already-proven
negative assertions that Manager does not create `NoteToSelf/SharedFileVault`
or `{team}/SharedFileVault`.
Its net-new assertion is that Vault-owned local state appears only under the
Vault root after Vault itself materializes it.

Assert:

- the relevant Core app and berth rows exist;
- Vault-owned local state appears only under the Vault root after Vault itself
  materializes it.

This test can extend the existing #116 coverage rather than duplicating all of
it.

### 4. Vault does not read Manager/Core databases

Add a focused guard test or static micro test over
`packages/shared-file-vault/shared_file_vault`.

This is a tripwire, not a proof.
It should fail if Vault code introduces obvious direct references such as:

- `SmallSeaCollectiveCore`;
- `core.db`;
- `NoteToSelf/Sync`;
- `Participants/{participant}/.../Sync/core.db`;
- `sqlite3.connect(...)` against Manager-owned paths.

It is acceptable for Vault to use its own local `checkouts.db`.
The guard should be narrow enough not to reject Vault-owned SQLite use.
The real evidence is the implementation shape plus integration tests that get
metadata through Hub sessions.

### 5. Hub berth storage scoping sanity check

Add a small sanity check or explicit code-review checklist item for Hub cloud
scoping.
The goal is not to redesign Hub storage.
It is to verify that Vault can safely remove the friendly team name from object
prefixes because the Hub session already scopes cloud operations to one berth.

Assert or document:

- `GET /session/info` returns the same `berth_id` that Hub cloud operations use
  for berth storage scoping;
- S3/MinIO bucket derivation, or equivalent backend scoping, is based on
  opaque berth/session metadata rather than `team_name`;
- Vault's remote object keys are interpreted inside that session scope.

If this sanity check fails, leave Vault cloud prefixes unchanged on this branch
and record the gap before implementation continues.

## Implementation Phases

### Phase 0: Baseline Audit

- Confirm the existing #116 Manager tests still prove registration does not
  create app directories.
- Confirm the existing #119 client helper raises
  `SmallSeaAppBootstrapRequired` for structured app-bootstrap rejections.
- Confirm `/session/info` currently returns the fields needed by #130:
  `participant_hex`, `berth_id`, `team_name`, and `app_name`.
- Audit whether Hub cloud operations are already scoped by the session berth,
  with backend storage boundaries derived from opaque berth/session metadata
  rather than Vault's friendly team-name object prefix.

Exit gate:
the branch plan names any missing session field and records whether Hub already
berth-scopes cloud operations before code changes begin.

### Phase 1: Red Tests

- Add the Vault materialization contract tests listed above.
- Keep assertions specific to the issue's invariants.
- Do not weaken existing Vault sync tests to make the red tests easier.

Exit gate:
the new tests fail for the expected reason, namely Vault still uses friendly
team names as local storage coordinates or lacks the new context helper.

### Phase 2: Vault Materialization Context and Local Coordinate Shape

- Add a Vault-owned context object needed to derive local coordinates from Hub
  session info.
- Validate required fields with clear errors.
- Keep `team_name` and `app_name` as display/identity labels.
- Change Vault path-helper signatures and local SQLite schemas/keys so the
  Vault team coordinate is `team_id`, not `team_name`.
- Move peer signal watermarks into Vault-local SQLite beside peer sync state by
  default, or keep them in config only with a concrete reason and a `team_id`
  key.

Exit gate:
Test #1's local-path and SQLite-key assertions pass, peer-sync rows key by
`team_id`, and watermark storage is decided, defaulting to Vault-local SQLite.
The implementation still has no Manager/Core DB read.

### Phase 2b: Cloud Prefix Follow-Through

Only run this phase if the Hub sanity check confirms cloud operations are
already berth-scoped by opaque session metadata.

- Remove `team_name` from Vault cloud object prefixes.
- Use within-session object keys such as `registry/` and
  `niches/{niche_name}/`, or keep only an app-internal namespace marker such as
  `vault/`.
- Thread the Vault materialization context through cloud-prefix construction
  rather than deriving object keys from the friendly team-name cache key or CLI
  argument.

Exit gate:
Hub-backed Vault sync tests pass with object keys that do not include
`team_name`.
If the sanity check failed, this phase is skipped and the follow-up is recorded.

### Phase 3: Wire Session-Backed Vault Paths

- Update session-backed sync/login flows to carry the materialization context
  into the updated Vault local path helpers.
- If Phase 2b ran, verify those same flows carry the context into cloud-prefix
  construction.
- Keep user-facing CLI arguments and UI routes friendly-name based where the
  current Hub APIs require a friendly team name.
- Avoid broad API churn unless the code becomes more confusing than the
  change.

Exit gate:
Vault's Hub-backed sync tests pass using the new local path coordinate and, if
Phase 2b ran, the new cloud-prefix coordinate.

### Phase 4: Manager Boundary Check

- Place or extend Test #3 in the Manager/Vault integration coverage so Manager
  still proves it only provisions access.
- Make sure no test fixture hides Vault materialization inside Manager setup.

Exit gate:
the Manager-side test suite proves Vault storage is created only by Vault.

### Phase 5: Docs and Review Notes

- Update `packages/shared-file-vault/spec.md` to describe the Vault-owned
  layout and the `participant_hex + team_id` coordinate rule.
- Update `architecture.md` only if the branch reveals a general principle not
  already captured by #116.
- Add `FOLLOW-UP.md` only if the work discovers concrete #8 requirements or a
  separate follow-up not already covered by #113-#117, #121, or #123.

Exit gate:
a reviewer can understand the branch from the tests plus the Vault spec.

## Validation Strategy

A skeptical reviewer should be able to verify the branch without trusting
intentions.

Run focused micro tests:

```bash
uv run pytest packages/shared-file-vault/tests/test_hub_sync.py
uv run pytest packages/shared-file-vault/tests/test_vault.py
uv run pytest packages/small-sea-manager/tests/test_create_team.py
```

Run broader affected suites if path helpers or sync flows changed deeply:

```bash
uv run pytest packages/shared-file-vault/tests packages/small-sea-manager/tests packages/small-sea-hub/tests
```

Run static checks:

```bash
rg -n "SmallSeaCollectiveCore|core\\.db|NoteToSelf/Sync|NoteToSelf.*core\\.db" packages/shared-file-vault/shared_file_vault
rg -n "def _[a-z][a-zA-Z0-9_]+\\([^)]*team_name" packages/shared-file-vault/shared_file_vault/vault.py
rg -n "SharedFileVault" packages/small-sea-manager/small_sea_manager packages/small-sea-manager/tests
git diff --check
```

Expected static-check interpretation:

- Vault runtime code should have no direct Manager/Core DB path references.
- Vault path-helper signatures should no longer use `team_name` as the local
  team coordinate.
- Manager tests may mention `SharedFileVault` as app registration data.
- Manager runtime code should not special-case `SharedFileVault`.

## Smart-Skeptic Evidence Checklist

At wrap-up, fill in concrete file paths and test names showing:

- [x] Vault derives local storage coordinates from `participant_hex` and
  Vault `team_id`, sourced from Hub `berth_id`.
  See `VaultMaterializationContext` and `_team_dir` in
  `packages/shared-file-vault/shared_file_vault/vault.py`, plus
  `test_create_niche`.
- [x] Friendly team names are display/identity data, not Vault's durable local
  team directory or SQLite-key coordinate.
  See `checkout`, `peer_sync`, and `peer_signal_watermark` schema keys in
  `vault.py`; `metadata.json` stores display metadata only.
- [x] Vault team contexts remain hard-separated.
  See `test_same_friendly_name_different_teams_do_not_share_storage`.
- [x] Participant contexts remain hard-separated.
  The participant directory remains the first storage layer, and the full
  shared-file-vault micro test suite exercises multi-participant sync.
- [x] One participant with two distinct team IDs and the same friendly team
  name gets distinct Vault storage.
  See `test_same_friendly_name_different_teams_do_not_share_storage`.
- [x] Hub-backed Vault cloud prefixes no longer include the friendly team name.
  Hub S3/MinIO operations are scoped by session berth, and
  `test_sync_config_roundtrip_and_remote_prefixes` now asserts `registry/`
  and `niches/docs/`.
- [x] Manager registration and activation create Core app/berth rows but no
  Vault working tree.
  This remains covered by the existing #116 Manager tests; the static Manager
  check only found `SharedFileVault` in tests.
- [x] Vault does not read Manager/Core databases directly.
  Static check:
  `rg -n "SmallSeaCollectiveCore|core\\.db|NoteToSelf/Sync|NoteToSelf.*core\\.db" packages/shared-file-vault/shared_file_vault`
  returned no matches.
- [x] No public metadata gap was found.
  `/session/info` already supplies `participant_hex`, `berth_id`,
  `team_name`, and `app_name`.
  `FOLLOW-UP.md` records only the offline local resolver improvement.
- [x] Existing Hub-backed Vault sync behavior still works.
  `uv run pytest packages/shared-file-vault/tests -q` passed.

## Follow-Up Policy

Create or update `.IN_PROGRESS/codex-issue-130-vault-materialization-check/FOLLOW-UP.md`
if implementation discovers work that should not land in this branch.

Likely follow-up buckets:

- #8 if Vault needs Hub-exposed metadata beyond current `/session/info`;
- #113 or #115 for duplicate friendly-name app identity repair;
- #114 if storage-provider indirection becomes necessary;
- #117 if a user-visible materialization opt-out is needed;
- #121 if same-device sighting assumptions break down.

Do not broaden issue 130 to absorb those futures.
