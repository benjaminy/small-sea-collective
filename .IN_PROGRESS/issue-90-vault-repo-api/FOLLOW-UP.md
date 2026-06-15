# Follow-ups discovered on branch issue-90-vault-repo-api

- **Comprehensive vault error boundary (new issue candidate).**
  This branch establishes the contract that vault's public API names *expected*
  failure modes as vault-domain errors, while *unexpected* low-level git failures
  surface as `cod_sync.repo.RepoError` (see PLAN.md Nuance 1). If we later decide
  the vault boundary should expose *only* vault-domain errors, add a pass that
  wraps `RepoError` from `init`/`stage`/`commit`/non-conflict-merge into a
  vault-level error type. Out of scope for #90 (which is about removing `gitCmd`
  leakage, not redesigning the error taxonomy).

- **Align manager `Repo.init` usage to canonical form (from the #90 issue body).**
  The manager branch used `Repo.init(path / ".git").with_work_tree(path)` as a
  stand-in for `git init path`. Vault uses the canonical `Repo.init(git_dir)`.
  Consider a small cleanup branch updating the manager calls for consistency.
