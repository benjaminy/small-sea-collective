The goal of this branch is simple: rename stations to **Berths**.

Currently the intersection of a Team and an App (often written `{Team}/{App}`) is called a _station_.
That name is serviceable, but it does not feel quite right.
This branch exists to replace it with a term that fits the nautical theme better and provides a clearer conceptual boundary.

After careful evaluation of various candidates (Berth, Mooring, Slip, Dock, Cove, etc.), **Berth** has been selected as the replacement.

## Why Berth?

- **Strong Nautical Fit**: A berth is an assigned place for a vessel, mapping perfectly to the resource allocation and access control nature of the concept.
- **Concise**: At 5 letters, it is shorter than "station" (7 letters) and fits cleanly into code (e.g., `berth_id`, `TeamAppBerth`).
- **Semantic Clarity**: It implies a specific, bounded, and *assigned* location, which matches how we use these intersections for data storage and permissions.
- **Linguistic Versatility**: It passes all phrase-testing (e.g., "request access to a berth", "berth permissions", "berth database") without awkward readings or verb interference.

## Execution Plan

The rename is mechanical but wide. To ensure a clean transition, we will follow this staged approach:

### 1. Code & Schema Rename
- **Class Names**: Rename `TeamAppStation` to `TeamAppBerth` across all packages (`small-sea-manager`, `small-sea-hub`, `small-sea-client`).
- **SQL Schema**:
    - Rename table `team_app_station` to `team_app_berth`.
    - Rename columns like `station_id` to `berth_id`.
    - Rename `station_role` to `berth_role`.
- **Variable Names**: Search and replace `station_id`, `station_id_hex`, and local `station` variables with their `berth` equivalents.
- **SQL Queries**: Update all raw SQL strings in `.py` and `.sql` files.

### 2. Documentation & Prose
- **Core Docs**: Update `architecture.md`, `AGENTS.md`, and `README.md`.
- **Package Specs**: Update `spec.md` files in `small-sea-hub`, `small-sea-manager`, etc.
- **General Prose**: Update `Documentation/*.md` and comments in code.

### 3. Tests & Templates
- **Test Assertions**: Update tests that check for "station" in error messages, logs, or JSON/SQL outputs.
- **HTML Templates**: Update any UI elements (like `members.html`) to use "Berth".

### 4. Cleanup
- Verify that no "station" references remain in active code or documentation (excluding the Archive directory).
- Update this `branch-plan.md` to mark the task as complete.

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
| Class: `TeamApp___`                 | `TeamAppBerth` ✓                   | `TeamAppSlip` ✓                      | `TeamAppMooring` — long              |
| SQL column: `____id`                | `berth_id` ✓                       | `slip_id` ✓                          | `mooring_id` — ok                    |
| SQL table: `____role`               | `berth_role` ✓                     | `slip_role` ✓                        | `mooring_role` — ok                    |

