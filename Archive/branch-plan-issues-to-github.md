# Migrate Local Issues to GitHub Issues

Retire the repo-local `Issues/` workflow and move active tracking to GitHub
Issues without losing project history.

## Branch Goal

Produce a migration plan that a skeptical maintainer could execute with
confidence.

At the end of this branch, we should have:
- A clear decision on what gets migrated vs. archived.
- A concrete mapping from local metadata to GitHub fields.
- A sequence that minimizes duplicate work and broken references.
- Explicit validation steps to confirm history is preserved.

## Current State

The repo contains:
- `20` open local issue files in `Issues/`
- `11` closed issue files in `Issues/Done/`
- Schema: `bug | task | idea | question | spec` (plus one `design` entry in `0017`).

Implementation note:
- GitHub should become the single canonical tracker after migration. The repo
  should not keep an active parallel issue workflow.

## Migration Strategy

### 1. Scope and Policy
- **Open Issues**: Migrate all as active GitHub Issues. They are actionable and
  contain current priority/type metadata.
- **Closed Issues**: Migrate all as closed GitHub Issues with a `legacy` label.
  This preserves searchable history in one place.
- **Non-Issue Types**: Treat `question`, `idea`, and `design` as first-class
  issues to maintain the decision backlog.
- **Normalization Rule**: Clean up only enough to remove confusion. Preserve the
  substance and references of the original files rather than rewriting history.

### 2. GitHub Mapping
- **Title**: From frontmatter `title`.
- **Body**: Preserve the existing markdown sections and references; add a short
  migration preamble rather than rewriting the issue into a new template.
- **Labels**:
  - `type:task`, `type:bug`, `type:idea`, `type:question`, `type:spec`, `type:design`
  - `priority:high`, `priority:medium`, `priority:low`
  - `legacy` (applied only to migrated closed issues)
- **Body Preamble**: Every issue gets a standard header:
  - Original Local ID and Filename.
  - Note that the issue was migrated from the repo-local tracker.
  - (For closed issues) `Original state: resolved in repo-local tracker.`
- **Mapping Record**: Maintain a migration table with local ID, source path,
  GitHub issue number, final state (`open`/`closed`), and any normalization note.

## Execution Plan

### Stage 1: Preparation and Normalization
1. **GitHub Setup**: Enable Issues and create the label set. Milestones are
   optional and should not block the migration.
2. **Inventory**: Generate a mapping table (ID, Title, Type, Priority) to catch
   stale wording or inconsistent types (e.g., normalizing `design` to `type:design`).
3. **Local Cleanup**: Lightly edit local markdown files to ensure frontmatter
   consistency and trim obsolete status notes before import.

### Stage 2: Dry-Run and Validation
1. **Payload Preview**: Generate a JSON preview of the GitHub API payloads.
2. **Review**: Spot-check formatting, label mappings, and preamble correctness.

### Stage 3: Execution (Import)
1. **Open Issues**: Create issues in priority order (`high` -> `medium` -> `low`)
   to roughly preserve urgency in the GitHub numbering.
2. **Closed Issues**: Create, label as `legacy`, and immediately close with a
   standard migration comment.
3. **Record Mapping**: Capture every resulting GitHub issue number in the
   migration table.

Recommended closing comment for migrated closed issues:
- `Closing as part of the migration from repo-local issues. This issue was already resolved before GitHub Issues became the canonical tracker.`

### Stage 4: Finalization and Archival
1. **Cross-Link**: Prepend "Migrated to GitHub #X" to the local markdown files.
2. **Freeze**: Update `Issues/README.md` to point to GitHub as canonical.
3. **Archive**: Move all local issue files to `Archive/local-issues/` to preserve
   original markdown history while removing them from the active path. Keep the
   original filenames unchanged for traceability.

## Validation

The migration is successful if:
- [ ] Every local file is accounted for (Migrated, Archived, or Excluded).
- [ ] No context (references, priorities, types) was lost during conversion.
- [ ] The repo has a single canonical tracker for active work.
- [ ] Historical material remains discoverable in both GitHub and `Archive/`.

**Checklist:**
- Count of open files matches imported open GitHub issues.
- Count of closed files matches imported closed GitHub issues.
- Every migrated local ID has a recorded GitHub issue number.
- Label mapping is consistent (no "mystery" labels).
- Spot-check: 1 high-priority task, 1 question, 1 legacy closed issue.

## Risks and Mitigations

| Risk | Mitigation |
| :--- | :--- |
| **Stale/Misleading Text** | Normalize open issues and perform a dry-run review. |
| **Broken References** | Include legacy IDs in GitHub bodies; keep an `Archive/` folder. |
| **Dual-Tracking Habit** | Archive local files immediately; update docs to be definitive. |
| **Label Clutter** | Stick to the minimal `type:` and `priority:` taxonomy initially. |

## Suggested Deliverables

- Migration inventory/mapping document.
- Normalization edits for local markdown files.
- Updated `Issues/README.md`.
- Archival of `Issues/` content to `Archive/local-issues/`.
