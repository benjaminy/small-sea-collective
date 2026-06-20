# Survey Notes

## High-leverage wording

The main misleading teammate-permission language was concentrated in:

- `README.md`
- `architecture.md`
- `packages/small-sea-manager/spec.md`
- `Documentation/apps-and-teams.md`
- `Documentation/open-architecture-questions.md`
- one stale open question in `packages/small-sea-hub/spec.md`
- a misleading membership-enforcement claim in `packages/ssc-files/spec.md`
- team-as-permission-context wording in `packages/the-hedgerow/README.md`
- team-wide authorization wording in the early Small Sea Live design documents

The repository already contained the correct decentralized substance, especially the statements that there is no membership oracle and that each participant has a local team view.
The problem was that nearby permission and authorization shorthand kept inviting a SaaS interpretation.

## Language intentionally retained

Authorization remains accurate for locally enforced boundaries, including:

- Bearer-token and PIN-mediated Hub sessions
- app-to-berth session scope
- provider credentials and provider-side capabilities
- OS and platform permissions
- cryptographic verification of devices and signed artifacts

The branch does not mechanically replace those terms.

## Compatibility surface intentionally retained

The following identifiers remain unchanged:

- `berth_role`
- `read-only`
- `read-write`
- `admin`, `contributor`, and `observer`
- API fields, database columns, exception names, and serialized values

The prose now explains those existing names as shorthand for readability, replication, and integration behavior.

## Prior work

`Archive/branch-plan-admin-control-clarification.md` records an earlier pass that established local-view and social-sync semantics.
This branch builds on that work by separating replication and integration from local Hub authorization more explicitly and by moving the distinction earlier in the top-level documentation.
