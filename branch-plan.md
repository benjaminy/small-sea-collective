The goal of this branch is simple: rename stations to **Berths**.

Currently the intersection of a Team and an App (often written `{Team}/{App}`) is called a _station_.
That name is serviceable, but it does not feel quite right.
This branch exists to replace it with a term that fits the nautical theme better and provides a clearer conceptual boundary.

After careful evaluation of various candidates (Berth, Mooring, Slip, Dock, Cove, etc.), **Berth** has been selected as the replacement.

Working definition:

- A **Berth** is the assigned place where a specific App lives within a specific Team.
- It is the unit of session scope, storage allocation, and per-member access control.

## Why Berth?

- **Strong Nautical Fit**: A berth is an assigned place for a vessel, mapping perfectly to the resource allocation and access control nature of the concept.
- **Concise**: At 5 letters, it is shorter than "station" (7 letters) and fits cleanly into code (e.g., `berth_id`, `TeamAppBerth`).
- **Semantic Clarity**: It implies a specific, bounded, and *assigned* location, which matches how we use these intersections for data storage and permissions.
- **Linguistic Versatility**: It passes all phrase-testing (e.g., "request access to a berth", "berth permissions", "berth database") without awkward readings or verb interference.
- **Right Level of Poetry**: It has some flavor, but not so much flavor that it becomes cute or obscure. That matters because this word will live in code, docs, schemas, and API discussions.

## Why not keep "Station"?

Renaming is only worth doing if the new term earns its cost.

- `Station` is not terrible, but it is generic enough that it never teaches the concept.
- It is also a weak fit for the repo's maritime vocabulary.
- `Berth` is not just more thematic; it is also more specific. The idea of an *assigned place* is exactly the nuance we want.
- If we are going to pay the rename cost at all, it should be for a name that feels materially better in code and prose. `Berth` clears that bar.

This also implies a useful discipline: if we discover late in the branch that `Berth` is awkward in real code, we should be willing to stop and keep `Station` rather than force a half-convincing rename.

## Compatibility stance

The important question is not just "what word do we like?" It is "where do we allow old terminology to survive?"

Recommended stance:

- **Prefer a one-word world**. Because this term is mostly internal and the project is still early, we should avoid carrying both `station` and `berth` indefinitely.
- **Rename the main vocabulary everywhere it matters**: type names, table names, column names, comments, specs, templates, and primary API prose.
- **Do not rename identifiers by accident**. Some uses of `station` are compatibility boundaries, not just prose. Those should be renamed intentionally or deliberately grandfathered, but never left half-converted.

Concrete implications:

- If `station_id`, `station_role`, `team_app_station`, or the `stations` key in wire formats stay as-is for compatibility, that should be an explicit policy decision.
- If we want the branch to truly finish the rename, the cleaner choice is to rename those as well and provide a migration story for existing local DBs.
- Bucket names, topic names, and storage identity do **not** need semantic changes as long as they still derive from the same underlying IDs. The noun changes; the identity does not.

My bias: since the repo is still early, a full rename is cleaner than an eternal mixed-vocabulary compromise. But if preserving existing local sandboxes matters, we should say so plainly and include a migration step instead of pretending search-and-replace is enough.

## Execution Plan

The rename is mechanical but wide. To keep it coherent, we should make a few decisions up front and then execute in stages.

### 0. Lock policy before editing

- Decide whether this is a **full vocabulary rename** or a **prose-and-types rename**.
- Decide whether we will support migration of existing local DBs or accept that developers must recreate them.
- Write that decision into this document before doing broad replacement work.

### 1. Code & Schema Rename

- **Core model names**: Rename `TeamAppStation` to `TeamAppBerth` in the Manager and Hub models.
- **Schema names**:
  - Rename `team_app_station` to `team_app_berth`.
  - Rename `station_role` to `berth_role`.
  - Rename columns like `station_id` to `berth_id` if we choose the full rename path.
- **Variable names**: Update `station`, `station_id`, `station_id_hex`, and similar locals/helpers where they represent the concept rather than a compatibility boundary.
- **Raw SQL**: Update all embedded SQL strings in `.py` and `.sql` files.

Hot spots already visible in the repo:

- `packages/small-sea-manager/small_sea_manager/provisioning.py`
- `packages/small-sea-hub/small_sea_hub/backend.py`
- `packages/small-sea-hub/small_sea_hub/server.py`
- `packages/small-sea-manager/small_sea_manager/sql/core_note_to_self_schema.sql`
- `packages/small-sea-manager/small_sea_manager/sql/core_other_team.sql`

### 2. Documentation & Prose

- **Core docs**: Update `architecture.md`, `AGENTS.md`, and any conceptual summaries.
- **Package specs**: Update `packages/small-sea-hub/spec.md`, `packages/small-sea-manager/spec.md`, and other active specs.
- **Supporting docs**: Update `Documentation/*.md` where the concept is explained or used in examples.
- **Code comments and docstrings**: Clean up comments that would otherwise leave the repo sounding split-brained.

### 3. Tests & Templates

- **Micro tests**: Update tests that assert on station-shaped names, JSON fields, SQL objects, or comments.
- **Templates/UI**: Update any HTML/UI fragments such as `packages/small-sea-manager/small_sea_manager/templates/fragments/members.html`.
- **Protocol expectations**: If API or JSON field names change, update tests that consume them rather than patching around failures piecemeal.

### 4. Cleanup

- Verify that no unintended `station` references remain in active code or docs.
- Exclude `Archive/` and other historical material unless we deliberately want those rewritten too.
- Update this `branch-plan.md` to record what compatibility choice we actually made and any migration notes.

## Rename traps

The easy failure mode here is a rename that is broad enough to be annoying but not broad enough to be complete.

- **Wire-format drift**: `station_id` appears in session info and notification-related paths. Decide once whether those become `berth_id`.
- **Schema drift**: Renaming prose while leaving `team_app_station` / `station_role` forever would make the codebase harder to explain, not easier.
- **Phrase drift**: Real repo phrases include "per-station", "same-station", "multi-station", and "station event". We should test those, not just the clean demo phrases.
- **Historical leakage**: Archive documents and issue files will continue to contain `station`. That is fine as long as active code/docs converge.
- **Blind replacement mistakes**: Some strings are examples, some are schema, some are API, and some are comments about old behavior. They should not all be treated the same way.

## Acceptance criteria

This branch should count as successful only if a bright skeptic would agree with all of the following:

- The repo now uses `berth` consistently in active code and docs, or any surviving `station` usage is explicitly justified as a compatibility exception.
- The chosen compatibility policy is documented, not merely implied by whatever happened during editing.
- The rename preserves the architecture: Hub remains the network gateway, and only Manager directly handles the core team DB.
- Core flows still work after the rename, especially team creation, invitation/acceptance, session opening, `/session/info`, and notification/watch behavior.
- Repo-wide search results for `station` are reviewed rather than blindly driven to zero.

Recommended micro test coverage after the rename:

- `packages/small-sea-manager/tests/test_create_team.py`
- `packages/small-sea-manager/tests/test_invitation.py`
- `packages/small-sea-manager/tests/test_hub_invitation_flow.py`
- `packages/small-sea-hub/tests/test_session_flow.py`
- `tests/test_small_sea_hub_smoke.py`
- `tests/test_watch_notifications.py`

## Analysis of Candidates (for historical context)

- **Berth** (Chosen)
  - Strengths: Strong nautical fit; implies assignment; short and distinctive.
  - Weaknesses: Potential (but minor) association with sleeping berths.

- **Station** (Original)
  - Strengths: Existing usage; clear meaning.
  - Weaknesses: Not nautical; abstract; generic.

- **Slip**
  - Strengths: Very nautical; short.
  - Weaknesses: "Slip permissions" and "slip role" have verb-interference ("to slip"). Less widely understood than berth.

- **Mooring**
  - Strengths: Strong nautical fit.
  - Weaknesses: Longer (7 chars); compound forms (e.g., `TeamAppMooring`) feel heavy.

- **Cove**
  - Strengths: Pleasant; natural boundary.
  - Weaknesses: Geographic rather than structural; lacks the sense of *assignment*.

- **Hold**
  - Strengths: Nautical; implies storage.
  - Weaknesses: Verb ambiguity ("hold permissions").

## Validation: front-runners in common phrases

| Phrase pattern                      | Berth                              | Slip                                | Mooring                              |
|-------------------------------------|------------------------------------|--------------------------------------|--------------------------------------|
| "request access to a ___"           | request access to a berth ✓        | request access to a slip ✓           | request access to a mooring ✓        |
| "all ___s in a team"                | all berths in a team ✓             | all slips in a team ✓                | all moorings in a team ✓             |
| "the Core ___"                      | the Core berth ✓                   | the Core slip ✓                      | the Core mooring — slightly heavy    |
| "the `{Team}/{App}` ___"           | the `{Team}/{App}` berth ✓         | the `{Team}/{App}` slip ✓            | the `{Team}/{App}` mooring — long    |
| "___ permissions"                   | berth permissions ✓                | slip permissions — ambiguous?        | mooring permissions — clunky         |
| "___ database"                      | berth database ✓                   | slip database ✓                      | mooring database — ok                |
| "___ role"                          | berth role ✓                       | slip role — odd                      | mooring role — ok                    |
| "open a session for the ___"        | open a session for the berth ✓     | open a session for the slip ✓        | open a session for the mooring — ok  |
| "per-___ permissions"               | per-berth permissions ✓            | per-slip permissions — odd           | per-mooring permissions — heavy      |
| "same-___ sessions"                 | same-berth sessions ✓              | same-slip sessions — odd             | same-mooring sessions — heavy        |
| "multi-___ sessions"                | multi-berth sessions ✓             | multi-slip sessions — awkward        | multi-mooring sessions — heavy       |
| "___ event"                         | berth event ✓                      | slip event — ok                      | mooring event — ok                   |
| Class: `TeamApp___`                 | `TeamAppBerth` ✓                   | `TeamAppSlip` ✓                      | `TeamAppMooring` — long              |
| SQL column: `____id`                | `berth_id` ✓                       | `slip_id` ✓                          | `mooring_id` — ok                    |
| SQL table: `____role`               | `berth_role` ✓                     | `slip_role` ✓                        | `mooring_role` — ok                  |

## Final recommendation

`Berth` still looks like the right choice.

More importantly, the branch should optimize for **consistency**, not merely for cleverness:

- pick one compatibility policy,
- apply it across code, schema, API vocabulary, and docs,
- validate it with focused micro tests, and
- leave behind a repo that teaches the concept more clearly than it did before.

That is the standard the rename should be held to.
