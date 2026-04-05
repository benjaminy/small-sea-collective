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

## Quick validation ideas

Before committing to a rename, we should test promising candidates in common phrases:

- "request access to a ___"
- "all stations in a team" -> "all ___s in a team"
- "the Core station" -> "the Core ___"
- "the `{Team}/{App}` station"
- "station permissions"
- "station database"

If a candidate sounds awkward in these phrases, that is probably a stronger signal than whether it sounds clever in isolation.
