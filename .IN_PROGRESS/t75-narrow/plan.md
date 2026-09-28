Implemented: preserve removal evidence and filter only current members and sender-key recipients.
Historical key readers, bootstrap, Constitution snapshots, and admission policy remain unchanged.
Expose active, removed, and disputed status from removal outcomes alone.

Validation compares stored evidence before and after removal, checks current membership and key recipients, and replays identical events in different orders with duplicates and pending parents.
The focused run passed these checks, pre-anchor fallback, status independence from mode ambiguity, and reconciliation.
The diff remains limited to provisioning, the membership template and CLI, micro tests, and these handoff documents.

Manager package tests completed, but localhost socket restrictions blocked MinIO-dependent checks.
Full-suite validation is recorded in notes.md.
Staging is blocked because the sandbox cannot write this worktree's Git metadata.
An unrestricted session must rerun the required checks, stage the changes, and commit on `t75-narrow` without amending.
