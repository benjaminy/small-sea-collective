The goal of this branch is simple: rename stations.

Currently the intersection of a Team and an App (often written `{Team}/{App}`) is called a _station_.
That name is serviceable, but it does not feel quite right.
This branch exists to find a better term, then do the tedious but important work of renaming code and documentation.

This document uses "station" for convenience, even though the whole point of the branch is to replace it.

One useful constraint is that users are not expected to see this term very often.
It is mostly a developer concept.
We expect normal users to think mostly in the context of a specific Team (in which case the thing is "just an App") or a specific App (in which case the thing is "just a Team").
So the term does not need mass-market appeal.
It does need to be clear, sturdy, and reasonably pleasant in code and technical writing.

## What the term needs to do

The replacement term should ideally:

- fit the marine / maritime / nautical naming theme better than "station"
- describe a bounded place where a Team and an App meet
- feel natural in phrases like "request access to a ___" and "the Core ___"
- be specific enough for developers without sounding too grand or too vague
- avoid strong collisions with existing technical jargon

## Candidates

- Station
  - Strengths:
    - It is the current term, so it already fits the repo's existing wording.
    - It suggests a distinct place or unit, which is at least directionally correct.
    - It is reasonably easy to use in code and prose.
  - Weaknesses:
    - It does not connect strongly to the marine / nautical theme.
    - It is fairly abstract.
    - It feels a bit generic for such a central concept.

- Berth
  - Strengths:
    - Strong nautical fit.
    - A berth is an assigned place, which maps nicely to resource allocation and access control.
    - It is short and distinctive in code, docs, and conversation.
  - Weaknesses:
    - Some people first think of a sleeping berth rather than a docking place.
    - It has a slightly physical feel for something that is partly logical / cryptographic.

- Mooring
  - Strengths:
    - Strong nautical fit.
    - Suggests a place where something is attached or anchored, which is not far from the Team/App intersection.
    - Has a little more texture than "station."
  - Weaknesses:
    - It is longer and a bit clunkier in code.
    - It may sound more like infrastructure than like an addressable unit.

- Slip
  - Strengths:
    - Very nautical.
    - A slip is an assigned place for a boat, which is a strong conceptual match.
    - Short and fairly memorable.
  - Weaknesses:
    - It is less widely understood than "berth" or "dock."
    - The ordinary-language meanings of "slip" are not especially helpful.

- Dock
  - Strengths:
    - Nautical and easy to understand.
    - Short, direct, and easy to use in code.
    - Conveys a place where something connects.
  - Weaknesses:
    - It feels more like shared infrastructure than a Team/App-specific unit.
    - It is somewhat imprecise compared with "berth" or "slip."

- Harbor
  - Strengths:
    - Pleasant maritime feel.
    - Suggests a protected place where activity happens.
    - Reads well in prose.
  - Weaknesses:
    - It sounds too large for the concept; a harbor feels closer to a Team or even the whole system.
    - It risks semantic overlap with the Hub, which is already the gateway / coordinating component.

- Port
  - Strengths:
    - Maritime and technical at the same time.
    - Very short and easy to work with in code.
  - Weaknesses:
    - It is massively overloaded in computing.
    - It could create confusion with network ports, software ports, or import/export language.
    - It sounds more like a gateway than a Team/App intersection.

- Anchorage
  - Strengths:
    - Strong nautical flavor.
    - Suggests a stable place where something can stay and operate.
    - Feels a little more specific than "harbor."
  - Weaknesses:
    - It is long.
    - It feels heavier and more geographic than the concept probably needs.

- Cove
  - Strengths:
    - Strong nautical fit. A cove is a small, sheltered body of water — naturally bounded.
    - Short and pleasant in code and prose.
    - Does not collide with existing technical jargon.
    - The "sheltered" connotation maps well to the access-control aspect.
  - Weaknesses:
    - It is geographic rather than structural — a natural feature, not an assigned unit.
    - It does not carry the sense of _assignment_ that "berth" or "slip" does. Nobody assigns you a cove.
    - It might sound too pastoral for an access-control boundary.

- Hold
  - Strengths:
    - Very nautical. A ship's hold is where cargo lives — a contained, bounded space.
    - Short and distinctive in code.
    - The "containment" and "storage" connotations fit well: each station has a database and is a resource boundary.
    - Maps naturally to the idea of allocated space within a larger vessel.
  - Weaknesses:
    - The verb "hold" creates ambiguity in prose. "The hold" is fine; "hold permissions" reads strangely.
    - It implies storage more than activity. A station is not just a container; sessions run there.
    - Compound forms are sometimes awkward: "hold role" could mean "hold onto a role."

- Instance
  - Strengths:
    - Immediately legible to developers.
    - Precise enough to communicate "a Team-specific realization of an App."
    - Very easy to use in code.
  - Weaknesses:
    - It completely drops the nautical theme.
    - It is dry and generic.
    - It may undersell the access-control and resource-boundary aspects of the concept.

## Current read

Early front-runners:

- `Berth` seems strongest if we want a more nautical term that still implies assignment and boundedness.
- `Station` remains a reasonable fallback if we decide the rename cost is not justified by the alternatives.
- `Slip` is interesting, but probably riskier because fewer developers will immediately know the intended meaning.

## Validation: front-runners in common phrases

The document proposed testing candidates in common phrases. Here is that exercise for the three strongest contenders.

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
| SQL table: `____role`               | `berth_role` ✓                     | `slip_role` ✓                        | `mooring_role` — ok                  |

Observations:
- **Berth** passes every phrase cleanly. No awkward readings. Compound forms are concise.
- **Slip** is nearly as good, but "slip permissions" and "slip role" have a faint verb-interference problem ("let something slip," "slip into a role").
- **Mooring** works semantically but adds syllables everywhere. In code, `mooring_id` and `TeamAppMooring` are noticeably longer than their berth/slip equivalents. Over hundreds of occurrences, that weight adds up.

## Code-level rename considerations

The rename touches more than prose. Some patterns worth noting:

- **Class names**: `TeamAppStation` (in both `provisioning.py` and `backend.py`) → `TeamAppBerth` (or whichever term wins). This is the most structurally important rename.
- **SQL tables and columns**: `team_app_station`, `station_id`, `station_role` all need migration scripts or at minimum careful search-and-replace with schema validation.
- **Variable names**: `station`, `station_id`, `ss_session.station_id` appear throughout Manager, Hub, and Client code.
- **Prose in specs and docs**: `architecture.md`, `packages/small-sea-hub/spec.md`, `packages/small-sea-manager/spec.md`, `Documentation/apps-and-teams.md`, and various issue files.
- **HTML templates**: `members.html` mentions "Core station role" in a table header.
- **Test assertions**: Several tests reference `TeamAppStation` by name or check for station-related strings.
- **AGENTS.md**: Mentions "Stations" as a core concept.

The rename is mechanical but wide: roughly 40 files contain "station" in some form. A staged approach — rename code first, then docs, then archive files — would keep diffs reviewable.

## Updated assessment

Berth remains the clear front-runner after the phrase-testing exercise. It is the only candidate that:
1. passes all validation phrases without any awkward readings,
2. is short enough for comfortable use in code (5 chars vs. station's 7),
3. carries the right semantic weight — an assigned, bounded, specific place, and
4. fits the maritime theme without being obscure.

The main remaining question is whether the sleeping-berth association bothers enough people to matter. In practice, context will disambiguate instantly — nobody reading `berth_role` or `TeamAppBerth` will think of bunk beds. And in maritime usage, "berth" meaning "an assigned docking place" is at least as common as the sleeping sense.
