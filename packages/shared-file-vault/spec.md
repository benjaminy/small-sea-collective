# Shared File Vault — Spec

## Purpose

A decentralized shared folder app built on Small Sea. Each shared folder
(a **niche**) belongs to a Small Sea team. Members of the team can
independently publish changes to a niche and pull changes from teammates,
without any central file-sync service.

Conceptually similar to Dropbox or Nextcloud Files, but the storage and
sync infrastructure is whatever each user already has through Small Sea
(their hub, their cloud remotes).

---

## Concepts

### Niche

A niche is a shared file tree scoped to a team. It is backed by a git
repository; Cod Sync carries it between participants via bundle chains.

### Niche registry

The set of niches that exist for a team is shared state. It is stored as
an SQLite database committed into a git repository and carried by its own
Cod Sync chain, one per team. Any team member can add a niche to the
registry; all members see it on their next pull.

Using git to carry an SQLite file means the merge story for the registry
is the same as for niche content: concurrent additions by different
participants converge automatically as long as the binary SQLite file
doesn't produce unresolvable conflicts. (Open question: whether to store
the registry as SQLite binary, as SQL text files git can diff/merge, or
to derive a local SQLite from a text-based canonical form.)

### Checkout

A checkout is a link between a niche and a directory on the local
filesystem where the user actually reads and writes files. The git metadata
lives inside the vault (via `--separate-git-dir`); the checkout directory
contains only the user's files.

Checkouts are **purely local state** — they are not shared with teammates.
**Each niche has at most one checkout on a given device.** A niche is either
not materialized locally (no checkout), or it is materialized at exactly one
path. To move a checkout to a different path, remove the existing one and
attach a new one.

**Merge operations require a clean checkout.** Before integrating changes
from a teammate (`merge_niche`, `pull_niche` when a checkout is attached),
the checkout must have no uncommitted tracked changes and no untracked files.
The user must publish or discard all local changes first. Untracked files are
treated the same as tracked changes — the rule is simply "the folder must be
clean", with no tracked/untracked distinction exposed to the user.

### Niche residency

A niche can be in one of three **residency modes** on a given device:

| Mode | Condition |
|------|-----------|
| **Remote only** | No niche git dir exists locally. The niche is known via the registry but has never been fetched. |
| **Cached** | The niche git dir exists locally — possibly with fetched peer refs or committed history — but no checkout is registered. |
| **Checked out** | The niche git dir exists locally and a checkout is registered in `checkouts.db`. |

Residency is about local materialization, not sync freshness. A niche can be
cached or checked out and still be behind a teammate.

Stale checkout registrations (registered path no longer exists on disk) are
reported as `StaleCheckoutError` and are not a distinct residency mode.

**State transitions:**

- *Remote only → Cached*: `create_niche`, `fetch_niche`, `pull_niche` (which
  creates the git dir before checking for a checkout).
- *Cached → Checked out*: `add_checkout`.
- *Checked out → Cached*: `remove_checkout`.
- *Checked out → Checked out*: `publish`, `push_niche`, `merge_niche`,
  `pull_niche`.
- *Cached → Cached*: repeated `fetch_niche` or other sync activity that
  updates refs without attaching a checkout.

No automatic transition back to *Remote only* exists; a local deletion flow
has not been implemented.

### Vault

The vault is the local storage root for all Shared File Vault data on a
device (or a user account on a device). It holds:
- The niche registry git repo per team (shared via Cod Sync)
- One git repo per niche (each shared via its own Cod Sync chain)
- Local checkout registrations

The vault is scoped to a **participant** — the Small Sea identity used on
this device. One participant per local user account is the overwhelming
common case, but the storage layout preserves a participant layer so that
switching participants (or having multiple) remains possible without a
re-architecture.

---

## Shared state (Cod Sync chains)

```
team "Photos"
  ├── niche-registry chain     ← which niches exist; each niche's metadata
  ├── niche "holiday-2025" chain
  ├── niche "receipts" chain
  └── ...
```

Each chain is an independent Cod Sync bundle sequence. Participants push
their local git repo to their cloud remote; teammates pull from it and
merge. The niche registry chain is merged the same way as niche content —
git handles convergence.

---

## Local storage layout

The vault root is expected to live in the platform-appropriate user data
directory (e.g. `~/Library/Application Support/SmallSea/FileVault` on
macOS, `%APPDATA%\SmallSea\FileVault` on Windows).

```
{vault_root}/
  {participant_hex}/
    checkouts.db              ← purely local: checkout path registrations
    {team_name}/
      registry/
        git/                  ← niche registry bare git repo (shared)
        codsync-bundle-tmp/
      niches/
        {niche_name}/
          git/                ← niche bare git repo (shared)
          codsync-bundle-tmp/
```

### checkouts.db schema

```sql
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE checkout (
    id            BLOB PRIMARY KEY,   -- UUIDv7
    team_name     TEXT NOT NULL,
    niche_name    TEXT NOT NULL,
    checkout_path TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (team_name, niche_name)    -- at most one checkout per niche
);
```

The `schema_version` table holds a single row. On version mismatch the
entire database is recreated from scratch (it is device-local and
reconstructable). No migration SQL is written.

---

## Operations

### Niche lifecycle

| Operation | Description |
|-----------|-------------|
| `create_niche` | Add a niche to the local registry repo and commit. Propagates to teammates on next push. |
| `list_niches` | Read the niche registry. Shows all niches the team has created, whether or not they are checked out locally. |

### Checkout management (local only)

| Operation | Description |
|-----------|-------------|
| `add_checkout` | Attach the single checkout of a niche to a local directory. Raises `DuplicateCheckoutError` if one already exists. |
| `remove_checkout` | Detach the checkout (does not delete files). |
| `get_checkout` | Return the checkout path or `None`. |

### Day-to-day

| Operation | Description |
|-----------|-------------|
| `publish` | Stage changes in a checkout and commit to the niche repo. |
| `status` | List uncommitted changes in a checkout. |
| `log` | Show recent commits for a niche. |

### Sync

| Operation | Description |
|-----------|-------------|
| `push_niche` | Push a niche to a cloud remote via Cod Sync. |
| `pull_niche` | Fetch from a cloud remote and merge into the local niche repo. Requires a clean checkout if one is attached; refreshes it after merge. |
| `fetch_niche` | Fetch from a peer and park the ref locally without merging. No checkout required. |
| `merge_niche` | Merge a previously parked peer ref. Requires a clean attached checkout. |
| `push_registry` | Push the niche registry to a cloud remote. |
| `pull_registry` | Pull the niche registry from a cloud remote and merge. |

---

## Open questions / known gaps

- **Participant identity**: `participant_hex` is a required parameter
  throughout the current API. Given that one participant per device is the
  common case, a higher-level API might wrap a default participant so
  callers don't have to pass it every time. The low-level vault functions
  should keep it explicit.

- **Joining a niche**: a new team member pulls the registry to discover
  which niches exist, then pulls each niche they want. No special
  "invitation" flow is needed at the vault level — team membership is
  enforced by Cuttlefish / hub access control.

- **Conflict resolution**: `pull_niche` merges via git. Auto-merge works
  for non-overlapping changes. Conflicts raise an untyped exception today;
  a typed `MergeConflictError` is aspirational.

- **Team membership enforcement**: nothing currently checks that the local
  participant is a member of the team. Full enforcement requires Cuttlefish
  integration (issue 0017).
