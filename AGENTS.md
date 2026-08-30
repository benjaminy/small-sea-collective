# Small Sea AI Agent Guidelines

As an AI agent working in this repository, you must follow these rules to maintain project integrity and follow existing conventions.

## Project Phase: Research, Not Production

**This is a research project, not a deployed product.**
There is no installed user base, production data, or service availability to preserve.

Actively identify designs that could fail under adversarial inputs, partial failure, concurrency, migration, or real-world operations.
Those observations are valuable even when the fix does not belong in the current change, and a focused GitHub issue is its normal home.
Do not add compatibility layers, rollout machinery, migration paths, defensive complexity, or operational recovery systems for a hypothetical deployment.
Implement a production-oriented safeguard now only when the user asks for it,
it is needed to validate the current research,
or postponing it would endanger developer data, credentials, or research integrity.

Existing code and documentation vary widely in maturity.
Treat repository precedent as evidence, not authority.
Before following an existing pattern, judge whether it expresses the intended design or is a research-stage shortcut that should be corrected.
If that distinction matters and the repository does not resolve it, ask a human rather than silently copying or correcting the pattern.

Prefer clean designs over compatibility with existing research artifacts unless the user asks otherwise.
Keep schema/version markers in place so future compatibility work remains possible.
The architectural mandates below describe the intended destination, not what must be built now.

## General Guidelines

**Simplicity first.**
Minimum code that solves the problem, nothing speculative.
No features beyond what was asked, no abstractions for single-use code, no unrequested configurability, no error handling for impossible scenarios.

**Surgical changes.**
Every changed line should trace to the user's request.
Do not improve adjacent code, do not refactor what is not broken, and match existing style even where you would do it differently.
Remove imports and definitions that your own changes orphaned; mention pre-existing dead code rather than deleting it.

**Ask instead of guessing.**
When multiple readings of a request would lead to materially different work, name the ambiguity and ask rather than picking silently.

**Argue on the merits.**
Do not treat the newest instruction as automatically correct.
When a request conflicts with earlier decisions or with evidence, name the conflict before acting, and say whether you are persuaded or merely deferring.
Change position because of a better argument, not because of who made it or when.

**Write concisely.**
Prefer one specific claim over three vague ones.
Cut filler and hype words like "robust", "comprehensive", and "seamless", but keep load-bearing qualifiers.
Summaries state what changed and why it matters; they do not narrate the process of getting there.

These guidelines bias toward caution over speed.
For trivial tasks, use judgment.

## Project Management Rules

- **Commits.** You may commit when every change is inside `.IN_PROGRESS/`, or when a human tells you to in conversation.
   Otherwise, prepare and stage changes but leave the commit to a human.
   Do not amend or rewrite existing commits.
- **Micro tests, not unit tests.** The project calls quick, developer-focused tests "micro tests".
   Use that term in discussion and documentation.
- **Branch docs.** For a nontrivial unit of work, keep all branch-scoped documents in `.IN_PROGRESS/{branch-nickname}/`,
   named after the current git branch with `/` replaced by `-`.
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

## Architectural Mandates
- **Hub as Gateway**: In production, all Small Sea internet traffic must go through the **Hub**.
   Going around the Hub to talk to cloud storage, any other service or peer device is bad.
   - This is *not* intended to limit what apps are allowed to do outside the scope of Small Sea.
- **Manager Database Exclusivity**: Only the `small-sea-manager` package is permitted to read/write the `{Team}/SmallSeaCollectiveCore` berth databases directly.
   All other apps must retrieve session and identity information via the Hub's API (`GET /session/info`).
- **Local-Only Testing**: During testing, avoid internet communication where possible. If tests require network interaction, use local mocks or services like MinIO.

## Contextual Knowledge
- Consult [architecture.md](architecture.md) for the core concepts (Teams, Apps, Berths) and the technical pillars (X3DH, Git-based sync).
- Familiarize yourself with the [README.md](README.md) to understand the "Why?" behind the project's local-first philosophy.

## Style Rules
- In plain text prose files (markdown, latex, etc) use semantic line breaks
   - Always line break after a complete sentence
   - Line breaks within sentences are discouraged
      - Only acceptable at natural pause points in very long sentences
   - Do not reformat existing text to follow this rule unless specifically instructed to do so
