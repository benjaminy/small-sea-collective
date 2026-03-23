# Issues

Open issues for the Small Sea Collective project. Loosely modeled after GitHub issues — plain markdown files, one per issue.

## Folder structure

- `Issues/` — open issues
- `Issues/Done/` — closed/resolved issues (move here when no longer actionable)

## Filename format

`NNNN-type-brief-title.md`

- `NNNN` — zero-padded sequential ID (e.g., `0001`, `0042`)
- `type` — one of: `bug`, `task`, `idea`, `question`, `spec`
- `brief-title` — kebab-case description

Examples: `0001-task-sync-orchestration.md`, `0009-bug-future-db-version.md`

## Issue format

Each file starts with a header block:

```
---
id: 0001
title: Short descriptive title
type: task          # bug | task | idea | question | spec
priority: high      # high | medium | low | (blank)
---
```

Followed by freeform markdown. Suggested sections:

- **Context** — background, why this matters
- **Work to do** — concrete steps or open questions
- **References** — relevant file paths, commits, or external docs

## Types

| Type | Use for |
|------|---------|
| `bug` | Incorrect behavior, crashes, unhandled cases |
| `task` | Concrete implementation work |
| `idea` | Suggestions, possible improvements |
| `question` | Open decisions, architecture choices |
| `spec` | Documentation and specification gaps |
