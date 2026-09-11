# Technical Writing Style Guide

You were trained on an ocean of obscurantist prose:
the kind Orwell warned about in "Politics and the English Language",
the kind Alan Sokal parodied in his hoax paper.
Prose like that aims to impress the reader, not to enlighten them.
Do not write it.

Write to be understood.
This is hard.
It is not a matter of picking shorter synonyms, and it is not a matter of defining every term before you use it.
It is a matter of deciding what you mean and then saying that.

Picture readers who are smart but do not yet know what you know.
Some models, from very different walks of life: Abraham Lincoln, Brian Kernighan, Ursula K. Le Guin, Mark Twain.
Each of them writes plain sentences about difficult things, and none of them sounds dumbed down.

These rules apply to everything you write here: documentation, plans, notes, comments, docstrings, commit messages, issues, and messages to the user.

**Name the actor and the action.**
Bad: "Credential replacement is subject to authorization by the management layer."
Good: "The Manager decides whether to replace a credential."

**State the point before the machinery.**
Bad: "The Hub, which provides the API through which apps obtain session and identity information, must be used when an app needs to determine the current user."
Good: "To identify the current user, an app asks the Hub through its session API."

**Say what happens, not that something happens.**
Bad: "Partial synchronization may result in inconsistencies in the local representation of shared state."
Good: "If synchronization stops halfway through, the device may have some of another device's changes but lack others."

**Keep the qualifications that matter. Cut the ones that don't.**
Bad: "Retries guarantee that changes reach every device."
Also bad: "The retry mechanism facilitates eventual propagation under appropriate operational conditions."
Good: "Retries can deliver the changes once the connection returns, provided the sending device still has them."

**Replace praise with behavior the reader can check.**
Bad: "The system provides robust and seamless conflict resolution."
Good: "When two devices make conflicting changes, the system preserves both versions and asks the user to choose."

**Report results, not your process.**
Bad: "I first examined the server module, then considered several approaches, and ultimately decided to add a check."
Good: "The server now rejects requests whose team ID does not match the session."

Before you finish any piece of prose, reread it against these rules.
Sentences that fail usually fail in the same way: a noun is doing the work a verb should do, or the reader learns how before they learn what.

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

**Human resolution is a design option.**
Before designing automatic conflict correction, evaluate detecting the problem, preserving the alternatives, and pausing for a human decision.
Identify whose work is blocked and what harm waiting causes; a person's own device may remain paused indefinitely if they choose not to resolve it.
Require automatic progress only when the task needs it, while keeping the problem visible and preserving the evidence needed for a meaningful decision.
See [Human-Scale Coordination](architecture.md#human-scale-coordination).

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
- **Branch lifecycle.** When starting, continuing, or finishing a nontrivial unit of branch work, read and follow [small-sea-branch-lifecycle](.agents/skills/small-sea-branch-lifecycle/SKILL.md).
   If your harness does not discover skills, read the linked file directly.
   Skip this for work that is conceptually very small in scope (cleanup, rename, etc).

## Architectural Mandates
- **Hub as Gateway**: In production, all Small Sea internet traffic must go through the **Hub**.
   Going around the Hub to talk to cloud storage, any other service or peer device is bad.
   - This is *not* intended to limit what apps are allowed to do outside the scope of Small Sea.
- **Manager Owns Management Decisions**: The Manager decides account registration, credential connect/replace/disconnect, storage allocation, app registration and activation, and membership.
   The Hub may read the `{Team}/SmallSeaCollectiveCore` berth databases directly for its framework responsibilities, and may write only the narrow set of execution results enumerated in [the Hub spec](packages/small-sea-hub/spec.md).
   All other apps must retrieve session and identity information via the Hub's API (`GET /session/info`), and open no Core database.
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
