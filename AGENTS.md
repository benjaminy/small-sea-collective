# Small Sea AI Agent Guidelines

As an AI agent working in this repository, you must follow these rules to maintain project integrity and follow existing conventions.

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

## Project Management Rules
- **Do NOT auto-commit.** You may prepare commits and stage changes, but always request explicit user approval before finalizing a git commit.
- **Micro Tests over Unit Tests.** The project refers to quick, developer-focused tests as "micro tests." Ensure you use this terminology in discussions and documentation.
- **Pre-alpha: do not spend effort on backward compatibility.**
   Prefer the cleanest design over migration shims or compatibility layers unless the user explicitly asks for them.
   Keep schema/version markers in place so future compatibility work remains possible.
- The typical workflow for a nontrivial unit of work should be the following.
   For work that is conceptually very small in scope (cleanup, rename, etc) this ceremony can be skipped.
   1. Make a git branch for the current task
   2. Make a branch folder for working docs in .IN_PROGRESS/{branch slug}
   3. In the branch folder make a PLAN.md file
      - My boss thinks AIs are not reliable enough for serious work yet.
         The validation part of the plan needs to be even better than would be expected on a great software engineering team.
	 How will the implementation convince a smart skeptic that:
         1. The goals of the branch have been accomplished
	 2. The general integrity of the repo (low coupling, maintainability, consistency, etc) has been maintained or improved
   4. Discuss, implement, debug, optimize
      - If any follow-up work to this branch is discovered (updates to existing GitHub issues or new issues to create), record them in .IN_PROGRESS/{branch slug}/FOLLOW-UP.md
   5. After work is complete on the branch, wrap-up by creating the following documents (from PLAN.md, NOTES.md, anything else that came up during implementation):
      - .IN_PROGRESS/{branch slug}/design-record-{branch slug}.md
         - This is a highly boiled down version of the plan with a focus on interesting choices a developer might want to revisit some years down the line.
      - .IN_PROGRESS/{branch slug}/review-note.md
         - A very brief note to put in the PR to orient a reviewer
   6. A human will manually do the PR, move files as appropriate and delete .IN_PROGRESS/{branch slug}

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
