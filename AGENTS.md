# Small Sea AI Agent Guidelines

As an AI agent working in this repository, you must follow these rules to maintain project integrity and follow existing conventions.

## Project Management Rules
- **Do NOT auto-commit.** You may prepare commits and stage changes, but always request explicit user approval before finalizing a git commit.
- **Micro Tests over Unit Tests.** The project refers to quick, developer-focused tests as "micro tests." Ensure you use this terminology in discussions and documentation.
- **Pre-alpha: do not spend effort on backward compatibility.**
   Prefer the cleanest design over migration shims or compatibility layers unless the user explicitly asks for them.
   Keep schema/version markers in place so future compatibility work remains possible.
- The typical workflow for a nontrivial unit of work should be:
   1. Make a git branch for the current task
   2. Make a branch folder for working docs in .IN_PROGRESS/{name of branch}
   3. In the branch folder make a PLAN.md file
      - My boss thinks AIs are not reliable enough for serious work yet.
         The validation part of the plan needs to be even better than would be expected on a great software engineering team.
	 How will the implementation convince a smart skeptic that:
         1. The goals of the branch have been accomplished
	 2. The general integrity of the repo (low coupling, maintainability, consistency, etc) has been maintained or improved
   4. Discuss, implement, debug, optimize
   5. After work is complete on the branch, wrap-up by creating the following documents (from PLAN.md, NOTES.md, anything else that came up during implementation):
      - .IN_PROGRESS/{name of branch}/FOLLOW-UP.md
         - This is a list of github changes that should be made:
	    - issues to close, comment on, or create
      - .IN_PROGRESS/{name of branch}/design-record-{name}.md
         - This is a highly boiled down version of the plan with a focus on interesting choices a developer might want to revisit some years down the line.
      - .IN_PROGRESS/{name of branch}/review-note.md
         - A very brief note to put in the PR to orient a reviewer

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
