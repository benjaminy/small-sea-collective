The goal of this branch is simple: rename stations.

Currently the intersection of a Team and an App (often written {Team}/{App}) is called a _station_.
This name is okay, but not great.
We're going to try to come up with something better, then go through the tedious process of changing all the code and documentation to the new name.

This doc will use "station" for convenience, even though the whole point of the branch is to come up with a better term.

One useful constraint is that users are not expected to see "station" very much.
It's more of a developer concept.
We expect normal users to mostly be thinking in the context of a specific team (in which case "station" is just an App) or a specific App (in which case "station" is just a Team).
So the term doesn't need to have super broad appeal beyonf developers.

Candidates:
- Station
   - Strengths:
      - The current term. Compatibility is not very important at this stage of the project, so this strength is not super important
      - Not *quite* as abstract/vague as other options like "space" or "zone"
   - Weaknesses:
      - Doesn't connect much with the marine/maritime/nautical naming theme
      - Pretty abstract
