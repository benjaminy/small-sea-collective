The point of this branch is to rename the package harmonic-merge.
For now let's brainstorm new names here.
Be clear about their advantages and disadvantages.

Here are some guidelines:
- Currently this package is focused on merging sqlite files, but it will expand to cover a much wider range of data formats, schemas, organizations.
- 3-way merge is a foundational element. Ours, theirs, ancestor/base.
- In the local-first community CRDTs have a lot of mindshare.
   This package is intended for different use cases where accuracy and simplicity are more important than speed.
- Of course the theme of the whole project is marine/nautical and it would be nice to stick with that.
- Compatibility doesn't matter for this project yet, so no need to worry about any kind of migration or breaking

## Brainstormed names

### Tidal / water-convergence

- **confluence** — where two rivers meet; literally "flowing together." Very on-the-nose for merging. Downside: namespace collision with Atlassian's wiki product.
- **tidemark** — the line where water meets land, a natural boundary/reconciliation point. Unique, short. Downside: doesn't scream "merge."
- **neap** — a neap tide is when sun and moon pull at right angles, producing a smaller, balanced tide. Evokes "two forces, one calm result." Downside: obscure; most people don't know the word.
- **brackish** — where fresh and salt water mix. Literally a merge of two sources. Downside: slightly negative connotation (murky).

### Navigation / charting

- **sextant** — the tool for triangulating position from multiple reference points. Three inputs → one answer maps nicely to 3-way merge. Downside: six-letter word starting with "sex" invites jokes.
- **bearing** — reconciling your position against known points. Short, clean. Downside: very generic word.
- **azimuth** — similar to bearing but more specific and distinctive. Downside: no obvious merge connotation.

### Marine biology / ecology

- **coral** — built by the slow, careful accumulation of many small organisms into one structure. Accuracy and patience over speed. Downside: commonly used by other projects.
- **barnacle** — attaches to everything, merges itself onto any surface. Memorable, funny. Downside: parasitic connotation.
- **polyp** — the individual organism that builds coral. Downside: medical connotation.

### Knot / rope / rigging

- **splice** — joining two ropes by interweaving their strands. This is exactly what merging does — interleaving two histories. Nautical, precise, evocative. Short. Downside: used in JS array API, but probably fine for a package name.
- **hawser** — a thick rope used to moor or tow ships; connects two things. Downside: obscure.
- **hitch** — a type of knot that joins rope to an object. Downside: very generic word.

### Cartography / depth

- **fathom** — a unit of depth measurement; also means "to understand deeply." Merging is about deeply understanding divergent states. Memorable, evocative. Downside: commonly used word.
- **soundings** — depth measurements taken to chart safe passage. Multiple readings reconciled into one chart. Downside: plural, a bit long.

### Top picks

1. **splice** — best metaphor-to-function match, nautical, short, memorable
2. **neap** — unique, short, subtle "two forces balanced" meaning
3. **sextant** — the triangulation / 3-way angle is compelling despite the name risk
4. **fathom** — "deep understanding of divergent state" is poetic
