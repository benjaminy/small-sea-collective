# Small Sea AI Agent Guidelines

As an AI agent working in this repository, you must follow these rules to maintain project integrity and follow existing conventions.

## Project Phase: Research, Not Production

**This is a research project, not a deployed product.**
There is no installed user base, production data, or service availability to preserve.
Do not expand current work to satisfy hypothetical operational constraints as though the project were already deployed.

The long-term goal is still to deploy Small Sea.
Actively identify designs that could fail under adversarial inputs, partial failure, concurrency, migration, or real-world operations.
Those observations are valuable even when they should not be implemented now.
A production risk can be important without belonging in the current change.
Do not treat missing production hardening as a blocker unless it invalidates the current research, violates an architectural mandate, or endangers developer data, credentials, or research integrity.

**Default to deferring deployment hardening.**
When a safety mechanism matters before deployment but not to the current research, treat a focused GitHub issue as its normal home.
Do not ignore the risk, and do not implement it opportunistically.

Use this decision rule:

- Implement what is needed to answer the current research question, preserve the intended architecture, or satisfy the user's explicit scope.
- Do not add compatibility layers, rollout machinery, migration paths, defensive complexity, or operational recovery systems solely for a hypothetical deployment.
- Surface important deferred safety or production-hardening work clearly and capture it for later without expanding the current change.
- Implement a production-oriented safeguard now only when the user requests it, it is necessary to validate the current research, or postponing it would endanger developer data, credentials, or the integrity of research results.

Existing code and documentation vary widely in maturity and quality.
Treat repository precedent as evidence, not authority.
Before following an existing pattern, judge whether it expresses the intended design or is a research-stage shortcut or mistake that should be corrected.
If that distinction matters and the repository does not resolve it, ask a human rather than silently copying or correcting the pattern.

Prefer clean designs over compatibility with existing research artifacts unless the user explicitly asks otherwise.
Keep schema/version markers in place so future compatibility work remains possible.
The production architectural mandates below define the intended destination; they do not imply that every production mechanism must be built during the research phase.

## General Guidelines

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

### 5. Argue on the Merits

**Do not treat the newest instruction as automatically correct.**

When a request conflicts with earlier decisions, stated constraints, or evidence:
- Name the conflict before acting.
- Compare the arguments, not the speakers or recency.
- Say when you are persuaded, and when you are merely deferring.
- If proceeding against your judgment, say so briefly and why.

Change position only because of new information or a better argument.

### 6. Concise, Precise Writing

**Make every sentence carry weight.**

In documentation, plans, comments, and discussion:
- Prefer one specific claim over three vague ones.
- Cut filler, restatements, and hype words like "robust", "comprehensive", and "seamless".
- Shorten paragraphs and sections when nothing is lost.
- Keep load-bearing qualifiers.
- Summaries state what changed and why it matters; they do not narrate the process of getting there.

## Project Management Rules
- **Do NOT auto-commit.** You may prepare commits and stage changes, but always request explicit user approval before finalizing a git commit.
- **Micro Tests over Unit Tests.** The project refers to quick, developer-focused tests as "micro tests." Ensure you use this terminology in discussions and documentation.
- For a nontrivial unit of work, copy `.IN_PROGRESS/InProgressBranchTemplate/` to `.IN_PROGRESS/{branch slug}/`
   (named after the current git branch)
   and maintain the documents there as their embedded instructions describe.
   For work that is conceptually very small in scope (cleanup, rename, etc) this can be skipped.
   A human will handle the PR and clean up the branch folder after merging.

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
