---
name: small-sea-branch-lifecycle
description: Maintain Small Sea branch documents and human handoffs when starting, continuing, or finishing nontrivial branch work. Skip conceptually very small changes such as cleanup or renames.
---

# Small Sea Branch Lifecycle

For a nontrivial unit of work, keep all branch-scoped documents in `.IN_PROGRESS/{branch-nickname}/` relative to the repository root.
Derive `branch-nickname` from the current git branch by replacing `/` with `-`.
Skip this for work that is conceptually very small in scope (cleanup, rename, etc).
Create each document when it has something to hold.
A human handles the PR and cleans up the branch folder after merging.

- `plan.md` — the implementation plan, handed to the implementer once planning is done.
   The validation story deserves the most attention.
   AIs are not yet trusted to be reliable for serious work, so the plan must say how the implementation will convince a smart skeptic that the branch's goals were met
   and that the general integrity of the repo (low coupling, maintainability, consistency) was maintained or improved.
   As steps are completed, deferred or abandoned, revise this document only briefly; substantial discussion belongs in `notes.md`.
- `notes.md` — anything that does not fit another document.
   May be empty.
- `follow-up.md` — the plan for GitHub issue changes to make after the implementation work.
- `design-record.md` — design choices worth remembering but not important enough for the real repo docs (`architecture.md`, etc).
   Write it near the end of the branch's life; a human copies it to `Archive/`.
   Decisions that belong in a proper design doc, and small facts that can be re-derived by reading the code, both stay out.
   Many branches have nothing in that middle ground, in which case the document should not exist.
- `final-commit-message.md` — a draft of the message for the branch's last commit, summarizing what the branch is about.
   At most a couple of paragraphs; not a design record or a catalog.
   A human condenses the commits and attaches it.
