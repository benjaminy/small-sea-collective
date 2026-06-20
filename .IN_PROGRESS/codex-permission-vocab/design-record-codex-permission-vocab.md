# Permission Vocabulary Design Record

## Decision

Describe teammate berth roles as local readability, replication, and integration policy rather than team-wide permissions granted by a central authority.
Retain authorization language for boundaries that a participant's local Hub, operating system, cryptographic verifier, or provider actually enforces.

Keep the existing `berth_role`, `read-only`, `read-write`, `admin`, `contributor`, and `observer` identifiers for now.
This branch changes the conceptual model in prose without prematurely changing serialized contracts or UI vocabulary.

## Operational meanings

- **Readability** concerns distribution of key material needed to interpret future berth updates.
- **Replication** concerns which histories a participant watches or fetches.
- **Integration** concerns which fetched changes enter a participant's local clone.
- **Admin** remains a shorthand role; operationally, it identifies a teammate whose Core publications peers normally integrate under the conventional mapping.
- **Local Hub authorization** governs which client software may act in which berth on one participant's device.

## Important caveat

The current Hub watcher still discovers signals from every teammate without consulting the session berth's role row.
Strictly avoiding ordinary pulls from `read-only` teammates is therefore an intended policy direction, not a completed runtime guarantee.
Merge-request discovery must remain observable when ordinary publications are not fetched, so role-aware replication should be designed together with issue #162 rather than added as an isolated filter.

## Why this boundary

Replacing every authorization-related word would erase real security boundaries and produce less precise documentation.
Renaming schema and public values before proposal discovery and role-aware replication settle would make code churn lead the design.
The conservative split makes the decentralized model louder while preserving stable implementation surfaces.
