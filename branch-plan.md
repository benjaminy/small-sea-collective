# Harmonic Merge Rename

Branch plan for `rename-harmonic-merge`.

## Branch Goal

Rename `harmonic-merge` to a name that better fits what the package actually is
and where it is headed:

- a library for careful 3-way reconciliation of structured data
- format-agnostic in ambition, even if SQLite is the first serious use case
- optimized for correctness, clarity, and explicit conflict handling rather
  than CRDT-style immediacy

This branch should end with one clear name choice, a clean in-repo rename, and
micro tests that make it obvious nothing behavioral regressed.

## Why Rename At All

`harmonic-merge` is not a bad name, but it is a little too flattering and a
little too vague.

- "harmonic" suggests effortless agreement, smoothness, and maybe even
  "conflicts somehow dissolve."
- The package is actually more honest than that. It takes `ancestor`, `ours`,
  and `theirs`, computes deltas, preserves non-conflicting changes, and keeps
  ours on direct conflict.
- The current name also does not signal the broader design described in the
  package README: canonicalize, merge structured representations, and import
  back into real storage formats.
- In this repo, names do real architectural work. `cod-sync`, `cuttlefish`,
  and `wrasse-trust` all help a reader guess a boundary. This package should do
  that too.

So the rename should make the package feel more like a deliberate tool and less
like a magical property.

## Naming Criteria

A strong replacement name should satisfy most of these:

1. It still makes sense once the package handles more than SQLite.
2. It evokes joining or reconciling divergent histories.
3. It does not imply real-time collaboration, CRDTs, or automatic consensus.
4. It fits the repo's marine / nautical language without becoming a joke.
5. It works as a distribution name, import name, directory name, and CLI prefix.
6. It is memorable when spoken out loud.
7. It feels like an infrastructure library, not a product or end-user feature.

## Recommendation

My current recommendation is `splice`.

Why it is strong:

- It is the closest metaphor to what the package does: separate strands are
  woven into one usable line without pretending they were never separate.
- It suggests craftsmanship rather than magic.
- It stays comfortably nautical.
- It generalizes beyond SQLite and beyond one schema.
- `splice-sqlite-merge` is a perfectly reasonable first CLI name, and future
  tools could follow the same shape.

Its main weakness:

- It is a common word, so before we fully commit, we should do one quick
  external namespace sanity check for package-name crowding.

My suggested decision rule:

- use `splice` unless a quick namespace check shows it is unreasonably crowded
- if `splice` looks too noisy, revisit `sextant` and `tidemark` as the best
  backups

## Shortlist

### 1. `splice`

Best overall fit.

- Exact "join two lines carefully" metaphor
- Strong nautical flavor
- Good match for deliberate, accuracy-first merge
- Slight downside: common word, and the metaphor is more "join" than explicitly
  "3-way join"

### 2. `sextant`

Best 3-way / triangulation metaphor.

- Feels like using multiple reference points to reach one trustworthy answer
- Distinctive and nautical
- Slight downside: less obviously about merging, and the name has a little
  avoidable joke energy

### 3. `tidemark`

Best poetic / unique option.

- Memorable
- Feels coastal and boundary-aware
- Slight downside: more about the line where things meet than the act of
  reconciliation itself

### 4. `fathom`

Best "deep understanding before acting" option.

- Conveys careful interpretation
- Works nicely for structured-data reasoning
- Slight downside: it sounds more analytical than combinational

### 5. `neap`

Best subtle option.

- Beautiful idea: balanced forces, moderated outcome
- Very compact
- Slight downside: too obscure for a package that other people will have to
  discover and remember

## Names To Reject

These are appealing at first glance but probably wrong for this branch:

- `confluence`: very direct, but too strongly associated with Atlassian
- `brackish`: mixing metaphor is good; negative / murky connotation is not
- `barnacle`: funny, but "attaches itself to things" is the wrong energy
- `coral`: attractive metaphor, but too indirect and widely reused
- `bearing` and `hitch`: both are too generic to do enough naming work

## Exact Rename Shape

Assuming we choose `splice`, the rename should be clean and total inside the
active codebase:

- `packages/harmonic-merge/` -> `packages/splice/`
- distribution name `harmonic-merge` -> `splice`
- import package `harmonic_merge` -> `splice`
- CLI `harmonic-sqlite-merge` -> `splice-sqlite-merge`
- git merge driver name `harmonic-sqlite` -> `splice-sqlite`

That also means updating:

- workspace dependencies in the repo root `pyproject.toml`
- `packages/small-sea-manager/pyproject.toml`
- manager provisioning code that installs the git merge driver
- package README and architecture docs
- tests and import paths
- `uv.lock`

Important non-goal:

- do not keep compatibility aliases unless we learn there is a real internal
  need for them; this repo is early enough that a clean break is better

## Scope

In scope:

- choose the new package name
- rename the package directory, distribution name, import name, and CLI
- update manager-side git merge driver installation
- update current-facing docs that describe the package
- update micro tests so the new name is exercised end to end

Out of scope:

- redesigning the merge algorithm
- expanding the package to new formats in this branch
- changing conflict policy
- inventing a new user-facing conflict UI
- polishing archived historical notes unless they actively mislead current work

## Implementation Order

### Phase 1: Lock the name

Before changing code, answer these explicitly:

- Are we choosing `splice`?
- If not, which backup name won and why?
- What are the exact distribution, import, and CLI spellings?
- What is the git merge driver key called in `.gitattributes` and git config?

This phase is about eliminating indecision, not touching files.

### Phase 2: Rename the package itself

Perform the mechanical rename:

- package directory
- import package
- package `pyproject.toml`
- package README heading and examples

The package should still have the same behavior and tests after this phase.

### Phase 3: Update dependents

Update everything that points at the old name:

- repo workspace metadata
- manager dependency metadata
- manager provisioning code that installs the merge driver
- comments and messages that mention the old executable
- tests that rely on the old package name or CLI name

### Phase 4: Update docs carefully

Docs should reflect the desired long-term role of the package:

- first concrete use case: SQLite merge driver
- broader mission: structured-data 3-way reconciliation
- explicit contrast with CRDT-style goals

This is a naming branch, so docs should become more precise, not more grandiose.

## Validation

We should be able to convince a bright critic with a small but solid proof set.

### Mechanical validation

- `rg "harmonic-merge|harmonic_merge|harmonic-sqlite-merge"` finds no active
  codebase references after the rename
- the new package path, import path, and script name all line up
- `uv.lock` reflects the new workspace package name

Archive references are allowed to remain if they are clearly historical.

### Behavioral validation

Run these micro tests:

- the renamed package's merge micro tests
- `packages/small-sea-manager/tests/test_merge_conflict.py`

Those prove both levels we care about:

- the core merge library still reconciles structured SQLite changes correctly
- manager provisioning still wires git to the renamed executable and the
  end-to-end merge flow still works

### Sanity checks

- import the renamed package successfully
- verify the renamed executable is discoverable in the dev environment
- confirm `.gitattributes` and git config use the new merge driver key

## Risks To Watch

- A too-poetic name could age badly once the package broadens beyond SQLite.
- A too-generic name could be hard to search for or easy to confuse.
- Renaming the executable without updating manager provisioning would silently
  break the merge-driver path.
- Renaming docs without clearly preserving the package boundary could make the
  package sound more ambitious than it really is today.

## Bottom Line

If we want the shortest path to a name that is vivid, honest, nautical, and
future-friendly, `splice` is the best current answer.

It says "careful joining" better than `harmonic`, it does not overpromise, and
it gives the repo a package name that sounds like a practical tool rather than
an unexplained abstraction.
