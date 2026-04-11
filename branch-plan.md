# Bootstrap NoteToSelf Through Hub-Owned Transport

Branch plan for `hub-bootstrap-transport`.
Primary tracker: #64.

## Goal

Make identity bootstrap work through **Hub-owned** cloud transport instead of
`LocalFolderRemote`, while keeping the current architectural rule intact:

- Manager still does not talk to the internet directly
- the joining device still starts without a normal NoteToSelf session
- the result is proven with local MinIO/S3 tests

This branch is about the **Hub transport boundary**, not about solving OAuth
bootstrap.

## Branch Claim

At the end of this branch, a critic should be able to say:

- Hub can fetch NoteToSelf bootstrap data before normal
  participant/session state exists on the joining device
- Manager still obeys “Hub as gateway”
- the flow works in a real cloud-shaped environment, not just a shared local
  filesystem
- OAuth bootstrap is still explicitly deferred

## Non-Goals

- no Dropbox bootstrap
- no GDrive bootstrap
- no new OAuth UX
- no redesign of identity-join trust
- no broad Hub session redesign
- no background NoteToSelf refresh/discovery UX

## Repo Findings

These are the important facts from the current codebase.

### 1. Hub startup is already easy

`SmallSeaBackend.__init__` can start on a blank root. It creates:

- `root_dir`
- `Logging/`
- `small_sea_collective_local.db`

So this branch does **not** need fake participant state just to start Hub.

### 2. Session creation is the real choke point

Normal session creation depends on live participant state:

- `request_session(...)` calls `_find_participant(...)`
- `request_session(...)` validates berth existence through `_resolve_berth(...)`
- `confirm_session(...)` resolves real `participant_id`, `team_id`, `app_id`,
  and `berth_id`

That is the hard part of this branch.

### 3. The S3 bootstrap read path mostly exists already

`proxy_cloud_file(...)` already supports anonymous S3 reads:

- no credentials required
- no participant DB reads required
- no deep adapter refactor required just to prove the concept

This is why S3/MinIO is the right proof harness.

### 4. Authorizing-side push is not the main refactor

The repo already has the right pattern for Hub-backed push:

- `TeamManager.push_team(...)` uses `SmallSeaRemote`
- `/cloud_file` and `/cloud/setup` already work with MinIO

So NoteToSelf push should be a modest extension of an existing pattern, not a
second major design problem.

### 5. Provisioning is still supposed to be local-only

Per [packages/small-sea-manager/spec.md](/Users/ben8/Repos/small-sea-collective/packages/small-sea-manager/spec.md#L23), the split is still:

- `provisioning.py`: local filesystem/SQLite work only
- session layer (`manager.py`, Hub client): cloud/network orchestration

This branch should preserve that split.

## Decisions

### Decision 1: Manager does not read cloud storage directly

No direct cloud reads in Manager. No “just use the Hub adapters as a library
from provisioning” shortcut.

### Decision 2: This branch needs bootstrap-scoped Hub auth

The joining device needs some kind of Hub-issued auth/transport capability for
bootstrap, but it must be **narrower than a normal session**.

This plan intentionally does **not** lock the exact mechanism yet.

What is locked:

- bootstrap transport cannot depend on normal participant/session/berth lookup
- bootstrap-scoped auth must not be accepted by ordinary session routes
- bootstrap-scoped auth must be limited to bootstrap transport only

### Decision 3: S3/MinIO is the required proof

This branch is S3/MinIO-only.

That is both a scope limit and the validation harness:

- it exercises real cloud-shaped transport
- it keeps tests local and deterministic
- it avoids mixing this branch with OAuth credential design

### Decision 4: OAuth bootstrap is deferred

Dropbox/GDrive bootstrap remains follow-up work. This branch should fail
clearly for unsupported providers rather than half-support them.

### Decision 5: Provisioning stays local-only

The joining-side fetch must move into the Manager/session layer or a new
bootstrap orchestration helper, not deeper into `provisioning.py`.

Provisioning should remain responsible for:

- pending join state
- decrypt/validate welcome bundle
- local directory/db setup
- signature verification after fetch
- final cleanup

### Decision 6: `remote_descriptor` must include `bucket`

The joining device cannot derive the NoteToSelf bucket before it has fetched
NoteToSelf.

So the welcome bundle must explicitly carry:

- `protocol`
- `url`
- `bucket`

For this branch, that is enough.

## What We Should Avoid

These are the biggest traps the current draft exposed.

### 1. Do not smuggle bootstrap into the normal `session` model

A placeholder row in the ordinary `session` table is risky unless the branch
also adds hard enforcement that ordinary routes reject it.

The plan should keep the requirement at the right level:

- bootstrap auth must be structurally narrower than ordinary session auth

### 2. Do not move cloud orchestration into provisioning

That would contradict the current Manager architecture and make later cleanup
harder.

### 3. Do not broaden the bootstrap auth surface too far

Even for S3-only, the branch should not casually create an auth artifact that
can proxy arbitrary cloud locations forever. The bootstrap capability should be
scoped as tightly as practical to the welcome-bundle descriptor and bootstrap
transport only.

## Current Best Direction

Without locking the exact refactor yet, the repo research points to this shape:

### Joining side

- Manager/session layer asks local Hub for bootstrap-scoped transport
- Manager/session layer fetches NoteToSelf through Hub-owned transport
- provisioning handles prepare/finalize around that fetch

### Authorizing side

- NoteToSelf push should use the same general Hub-backed pattern as
  `push_team(...)`
- this is a supporting change, not the main design question

### Hub side

- Hub gets a bootstrap-scoped transport/auth path
- ordinary session-only routes reject that bootstrap auth
- existing anonymous S3 read logic should be reused where possible

## Scope

### In scope

- add a bootstrap-capable Hub transport path
- add bootstrap-scoped Hub auth that is narrower than normal sessions
- include `bucket` in the welcome bundle descriptor
- add authorizing-side NoteToSelf push through Hub for the MinIO proof path
- add MinIO/S3 micro tests for both push and bootstrap fetch
- update docs/specs

### Out of scope

- OAuth bootstrap credential design
- real provider-auth UX for a brand-new device
- automatic NoteToSelf refresh after bootstrap
- background discovery/watch flows
- making every bootstrap transport path share one final abstraction

## Validation

The branch should not be considered complete unless it proves all of these:

- the welcome bundle carries `protocol`, `url`, and `bucket`
- the authorizing device can publish NoteToSelf through Hub-owned cloud
  transport in MinIO/S3 tests
- the joining device can fetch NoteToSelf through Hub-owned bootstrap
  transport without a preexisting normal NoteToSelf session
- the joining device does not need a fake fully initialized NoteToSelf DB
  before fetch
- bootstrap-scoped auth is rejected by ordinary session-only routes such as
  `/session/info` and `/cloud_file`
- unsupported bootstrap providers fail clearly instead of silently falling
  back to local-only assumptions
- existing `LocalFolderRemote` identity-bootstrap tests still pass unless we
  intentionally replace them

## Implementation Order

### Phase 1: Lock the Hub transport boundary

- add bootstrap-scoped Hub auth/transport
- prove it works on a blank Hub root
- prove ordinary session routes reject it

### Phase 2: Authorizing-side NoteToSelf push through Hub

- add a NoteToSelf push path in the Manager/session layer
- prove NoteToSelf can be published to MinIO through Hub

### Phase 3: Joining-side bootstrap fetch through Hub

- move joining-side fetch orchestration into the session layer
- keep provisioning local-only
- fetch NoteToSelf through Hub-owned bootstrap transport

### Phase 4: End-to-end MinIO bootstrap

- full round-trip through Hub-owned transport
- verify the existing signed welcome-bundle checks still pass

### Phase 5: Docs

- update Hub and Manager specs
- document S3/MinIO-only support for bootstrap
- document OAuth deferral explicitly

## Risks

- **Bootstrap auth accidentally becomes “just another session.”**
  Mitigation: make route separation a required validation point.
- **Network code leaks into provisioning.**
  Mitigation: keep the provisioning/session-layer split explicit in the plan.
- **The branch turns into an OAuth design branch.**
  Mitigation: keep MinIO/S3 as the hard scope boundary.
- **Authorizing-side push and joining-side fetch get tangled together.**
  Mitigation: treat authorizing-side push as a supporting task, not the core
  refactor.

## Implementation Design

These answer the deferred questions from the branch-shape draft, informed by
a deep code analysis.

### Bootstrap auth: dedicated `bootstrap_session` table

A separate table gives enforcement by construction. Normal `_require_session`
queries the `session` table — it will never find a bootstrap token. No "add a
kind check everywhere" fragility.

**New table** (Hub schema v50):
```sql
CREATE TABLE IF NOT EXISTS bootstrap_session (
    id BLOB PRIMARY KEY,
    token BLOB NOT NULL,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    bucket TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
```

The bootstrap session carries the exact `{protocol, url, bucket}` it is
scoped to. The caller only supplies `path` at download time.

**New backend methods:**
- `create_bootstrap_session(protocol, url, bucket, ttl_seconds=600) → bytes`
- `lookup_bootstrap_session(token_hex) → BootstrapSession`
- `bootstrap_cloud_download(token_hex, path) → (ok, data, etag)`

`bootstrap_cloud_download` reads `{protocol, url, bucket}` from the session
row. For S3, it does anonymous read (same pattern as `proxy_cloud_file`). For
unsupported protocols, it raises immediately.

**New endpoints:**
- `POST /bootstrap/session` — create bootstrap session (no auth required,
  same trust model as `POST /sessions/request`)
- `GET /bootstrap/cloud_file` — download via bootstrap session

**New auth dependency:**
```python
def _require_bootstrap_session(x_bootstrap_token: str = Header(...)):
    ...
```

Uses a different header (`X-Bootstrap-Token`) so there is zero overlap with
`Bearer` auth. Normal session endpoints never see this header; bootstrap
endpoints never see `Authorization: Bearer`.

**Schema migration** follows the existing pattern: add an
`if user_version == 49:` block in `_initialize_small_sea_schema`, bump to
v50. Update `hub_local_schema.sql` for fresh installs.

**ORM model**: `BootstrapSession(Base)` in `backend.py`, consistent with
`SmallSeaSession` and `PendingSession`. The Hub uses ORM for hub-local
tables and raw SQL for participant/team DBs.

### Split `bootstrap_existing_identity` into prepare / fetch / finalize

Current function (provisioning.py:639-754) does three things. The split:

**`provisioning.prepare_identity_bootstrap(root_dir, welcome_bundle_b64)`**
(current lines 641-706)
- load pending join state
- decrypt welcome bundle (AEAD open)
- validate device_id, public_key, expiry
- create participant directory, FakeEnclave, local DB
- write device key secrets
- init git repo at `NoteToSelf/Sync` (but do NOT fetch)
- return context dict: `{participant_hex, remote_descriptor, sync_dir,
  bundle, signed_bundle, pending_artifact, pending_state}`

**Session layer does the fetch** (current lines 708-716)
- request bootstrap session from Hub
- build CodSync remote pointing at `GET /bootstrap/cloud_file`
- run `cod.fetch_from_remote(["main"])` and `git checkout main`

**`provisioning.finalize_identity_bootstrap(root_dir, context)`**
(current lines 718-754)
- open the now-fetched `core.db`
- look up `user_device.signing_key` for `authorizing_device_id_hex`
- verify the welcome bundle signature
- if fail: mark untrusted, raise
- if pass: clean up pending keys and join state
- return result dict with confirmation string

Data dependency check: finalize needs `bundle`, `signed_bundle`, and
`pending_artifact` from prepare. These are all in the context dict. The
signature verification reads from the fetched `core.db` which exists after
the fetch step. Clean split.

### Authorizing-side push through Hub

Follow the `push_team` pattern exactly (manager.py:262-286):

```python
def push_note_to_self(self):
    from cod_sync.protocol import CodSync, SmallSeaRemote
    session = self._get_or_open_session("NoteToSelf", mode="passthrough")
    session.ensure_cloud_ready()
    repo_dir = (self.root_dir / "Participants" / self.participant_hex
                / "NoteToSelf" / "Sync")
    remote = SmallSeaRemote(session.token, base_url=self.client._base_url)
    cs = CodSync("origin", repo_dir=repo_dir)
    cs.remote = remote
    cs.push_to_remote(["main"])
```

**Push ordering constraint**: the push must complete before the welcome
bundle is returned. Currently (line 596) provisioning pushes then builds
the bundle. After this branch, provisioning does NOT push — it returns
the sealed bundle without pushing. `TeamManager.authorize_identity_join`
must push first, then return the bundle:

```python
def authorize_identity_join(self, join_request_artifact_b64, **kwargs):
    result = provisioning.authorize_identity_join(
        self.root_dir, self.participant_hex,
        join_request_artifact_b64, **kwargs,
    )
    self.push_note_to_self()  # push BEFORE returning the bundle
    return result
```

This means `provisioning.authorize_identity_join` must stop calling
`_push_note_to_self_to_local_remote`. The git commit (lines 591-595) stays
in provisioning (it's local). The push moves out.

**`ensure_cloud_ready`** is required for S3 — it creates the bucket with
public-read policy. The invitation test already calls this via `/cloud/setup`.
NoteToSelf push through Hub needs the same call.

**CodSync signing**: NoteToSelf push is currently unsigned (line 295:
`cod.push_to_remote(["main"])` with no signing key). No change needed.

### Adding `bucket` to `remote_descriptor`

The NoteToSelf bucket name is `ss-{berth_id.hex()[:16]}`. The berth_id is
in the `team_app_berth` table, created during `create_new_participant`
(line 474-475).

Update `_single_note_to_self_remote_descriptor` to query the berth:

```python
berth_row = conn.execute(
    "SELECT tab.id FROM team_app_berth tab "
    "JOIN app a ON a.id = tab.app_id "
    "WHERE a.name = 'SmallSeaCollectiveCore'"
).fetchone()
bucket = f"ss-{berth_row[0].hex()[:16]}"
```

Add `"bucket": bucket` to the returned dict.

### Bootstrap CodSync remote

The joining device needs a CodSync remote for `GET /bootstrap/cloud_file`.
Options:

1. **New `BootstrapProxyRemote` class** — thin, ~30 lines, uses
   `X-Bootstrap-Token` header, only implements download (read-only)
2. **Mode on `ExplicitProxyRemote`** — add a `bootstrap_token` parameter
   that switches the endpoint and header

Option 1 is cleaner because it avoids conditionals in an existing class.
The remote only needs `_download`, `get_link`, `get_latest_link`, and
`download_bundle` — all inherited from `CodSyncRemote` base with
`_download` overridden.

### Joining-side module-level wrapper

Currently `manager.py:16-18`:
```python
def bootstrap_existing_identity(root_dir, welcome_bundle_b64):
    return provisioning.bootstrap_existing_identity(root_dir, welcome_bundle_b64)
```

After this branch, this becomes the orchestration point:
```python
def bootstrap_existing_identity(root_dir, welcome_bundle_b64,
                                hub_port=11437, _http_client=None):
    ctx = provisioning.prepare_identity_bootstrap(root_dir, welcome_bundle_b64)
    descriptor = ctx["remote_descriptor"]
    protocol = descriptor.get("protocol")
    if protocol == "localfolder":
        # Legacy path: direct local fetch
        _do_local_fetch(ctx)
    else:
        # Hub-mediated fetch
        client = SmallSeaClient(port=hub_port, _http_client=_http_client)
        bootstrap_token = client.request_bootstrap_session(
            protocol=descriptor["protocol"],
            url=descriptor["url"],
            bucket=descriptor["bucket"],
        )
        _do_hub_fetch(ctx, bootstrap_token, client)
    return provisioning.finalize_identity_bootstrap(root_dir, ctx)
```

The `localfolder` path stays for existing tests. The Hub path is the new
code. Both converge on `finalize_identity_bootstrap`.

### What happens to existing tests

**`test_localfolder_identity_bootstrap_roundtrip`**: The push inside
`provisioning.authorize_identity_join` goes away. The test uses
`alice_manager.authorize_identity_join(...)` which calls
`TeamManager.authorize_identity_join`. The TeamManager method now calls
`self.push_note_to_self()`. But TeamManager needs a Hub session for push.

Two options:
1. **Keep `localfolder` push in provisioning** as a legacy path: if protocol
   is `localfolder`, provisioning still pushes directly. Hub push only
   happens for Hub-backed protocols.
2. **Route all push through Hub**: even `localfolder` tests use Hub. This is
   more consistent but requires updating the test to set up a Hub + session.

Option 1 is lower-risk for this branch. The existing test keeps working.
The new MinIO test proves the Hub path. A future branch can unify if desired.

### Test plan for the new MinIO test

Follow the `test_hub_invitation_flow` pattern (already proven):

```python
def test_minio_identity_bootstrap_roundtrip(playground_dir, minio_server_gen):
    minio = minio_server_gen(port=19850)
    root1 = ...  # authorizing device
    root2 = ...  # joining device

    # Shared Hub (in-process, auto-approve)
    backend = SmallSeaBackend(root_dir=str(root1), auto_approve_sessions=True)
    app.state.backend = backend
    http = TestClient(app)

    # Create participant with S3 cloud storage
    alice_hex = create_new_participant(root1, "Alice")
    alice_nts_token = _open_session(http, "Alice", "NoteToSelf", mode="passthrough")
    backend.add_cloud_location(alice_nts_token, "s3", minio["endpoint"],
                               access_key=minio["access_key"],
                               secret_key=minio["secret_key"])

    # Join request from blank device
    join_request = create_identity_join_request(root2)

    # Authorize: admits device, commits, pushes NoteToSelf through Hub
    alice_manager = TeamManager(root1, alice_hex, _http_client=http)
    welcome = alice_manager.authorize_identity_join(
        join_request["join_request_artifact"])

    # Bootstrap: joining device fetches through Hub bootstrap transport
    #   (uses separate Hub pointed at root2, or same Hub with root2 paths)
    bootstrap = bootstrap_existing_identity(
        root2, welcome["welcome_bundle"],
        _http_client=http)

    assert bootstrap["participant_hex"] == alice_hex
    # ... verify shared DB, device keys, confirmation string
```

**Key detail**: the test uses one in-process Hub with `TestClient`. The
joining device calls `POST /bootstrap/session` and
`GET /bootstrap/cloud_file` through the same Hub. This works because
bootstrap sessions don't read from participant state.

**But wait**: the Hub's `SmallSeaBackend` has `root_dir` set to `root1`
(the authorizing device). The joining device's `root2` is a different
directory. The Hub doesn't need to know about `root2` — it just needs to
proxy the S3 read. The joining device's local state setup (prepare/finalize)
happens via provisioning, which reads/writes `root2` directly.

This is actually fine because:
- `POST /bootstrap/session` doesn't read any participant state
- `GET /bootstrap/cloud_file` does an anonymous S3 read (no participant
  state needed)
- The participant data in `root2` is managed by provisioning, not Hub
