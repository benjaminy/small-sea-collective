# Small Sea AI Agent Guidelines

As an AI agent working in this repository, you must follow these rules to maintain project integrity and follow existing conventions.

## Project Management Rules
- **Do NOT auto-commit.** You may prepare commits and stage changes, but always request explicit user approval before finalizing a git commit.
- **Micro Tests over Unit Tests.** The project refers to quick, developer-focused tests as "micro tests." Ensure you use this terminology in discussions and documentation.
- The typical workflow should be:
   1. Make a branch for the current task
   2. Iterate on the branch-plan.md document
      - AIs are not reliable. The plan had better have really excellent validation. How are you going to convince a bright critic that this plan:
         1. Accomplishes the goals of the branch
	 2. Maintains or improves the general integrity of the repo
   3. Implement, debug, optimize
   4. If anything significant changed while working, update branch-plan.md and move it to Archive/branch-plan-{BRANCH_NAME}.md
   5. Merge to main

## Architectural Mandates
- **Hub as Gateway**: In production, the ONLY Small Sea component allowed to communicate with the internet is the **Hub**.
   Any other component (Manager, internal packages) must use the Hub API for all network-related activity.
   - This is *not* a restriction on what apps are allowed to do outside the scope of Small Sea.
- **Manager Database Exclusivity**: Only the `small-sea-manager` package is permitted to read/write the `{Team}/SmallSeaCollectiveCore` berth databases directly.
   All other apps must retrieve session and identity information via the Hub's API (`GET /session/info`).
- **Local-Only Testing**: During testing, avoid internet communication where possible. If tests require network interaction, use local mocks or services like MinIO.

## Contextual Knowledge
- Consult [architecture.md](architecture.md) for the core concepts (Teams, Apps, Berths) and the technical pillars (X3DH, Git-based sync).
- Familiarize yourself with the [README.md](README.md) to understand the "Why?" behind the project's local-first philosophy.
