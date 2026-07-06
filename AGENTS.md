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
- **Project status: still research. Do not spend effort on backward compatibility.**
   Prefer the cleanest design over migration shims or compatibility layers unless the user explicitly asks for them.
   Keep schema/version markers in place so future compatibility work remains possible.
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
