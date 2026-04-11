# Bootstrap NoteToSelf Through Hub-Owned Transport

Branch plan for `hub-bootstrap-transport`.
Primary tracker: #64.

## Context

Identity bootstrap now works end-to-end in the local-only case:

1. the joining device creates a join request
2. the existing device admits it
3. the existing device returns an encrypted, signed welcome bundle
4. the joining device pulls NoteToSelf
5. the joining device verifies that the bundle was signed by a device in the pulled identity

That flow currently relies on `LocalFolderRemote`, which is useful for proving the trust model but not for real cloud-backed use.

The joining device still has the same important starting constraints:

- no existing identity
- no normal NoteToSelf DB/session state
- no device-local cloud credentials yet
- only the welcome bundle and its `remote_descriptor`

The authorizing device, by contrast, is a normal live installation with a Hub, local credentials, and a NoteToSelf repo it can already push and read.

## Branch Goal

Refactor the architecture so that the **Hub** can perform NoteToSelf bootstrap cloud transport before normal participant/session initialization exists, and prove that path with a cloud-shaped local test harness.

More concretely, after this branch:

- the joining device's **Manager** still does not talk to the internet directly
- the joining device can ask its local **Hub** to fetch NoteToSelf bootstrap data using only the welcome bundle's remote descriptor
- the authorizing device can push the relevant NoteToSelf state through normal Hub-owned cloud transport
- the flow is proven with local MinIO/S3-style tests rather than `LocalFolderRemote`

## What This Branch Is Really About

This branch is **not** mainly about S3.

S3/MinIO is just the safest proof environment because it lets us validate the transport architecture without also solving OAuth bootstrap.

The real problem is:

> How can Hub perform the cloud read/write work needed for identity bootstrap before normal NoteToSelf participant/session state exists on the joining device?

That is the load-bearing design question for this branch.

## Key Decisions

### Decision 1: Manager does not read cloud storage directly

This branch should preserve the repo's architectural rule:

- the Hub is the only Small Sea component that talks to the internet
- Manager may orchestrate bootstrap, but it must do so through Hub-owned APIs or Hub-owned library surfaces

So this branch should **not** solve bootstrap by teaching Manager to use cloud adapters directly.

### Decision 2: The branch centers on a bootstrap-capable Hub path

The branch goal is to create a **bootstrap-only transport path** in or for the Hub that:

- does not require a preexisting NoteToSelf session
- does not require NoteToSelf DB reads on the joining device
- can consume the welcome bundle's remote descriptor directly
- is clearly separated from normal post-bootstrap session flows

This is the main architectural outcome we want from the branch.

### Decision 3: S3/MinIO is the required proof

This branch should prove the new transport path with MinIO / S3-shaped local tests.

That is a scope limiter, but more importantly it is a **validation strategy**:

- MinIO gives us a real cloud-shaped path
- the tests stay local and deterministic
- we avoid mixing the Hub refactor with OAuth bootstrap policy

### Decision 4: OAuth bootstrap is explicitly deferred

Dropbox / GDrive / other credential-bearing bootstrap flows are out of scope for this branch.

This branch should leave them as a follow-up, not pretend they are almost solved.

## Non-Goals

- no Dropbox bootstrap
- no GDrive bootstrap
- no new OAuth UX
- no redesign of the identity-join trust model
- no changes to the signed welcome-bundle verification logic beyond whatever small descriptor additions are required
- no general “make bootstrap work for every cloud backend” claim

## Result We Want

At the end of this branch, a critic should be able to say:

- yes, Hub can now handle NoteToSelf bootstrap transport before normal identity/session state exists
- yes, Manager still obeys the “Hub as gateway” rule
- yes, the result works in a real cloud-shaped environment, not just shared local filesystem tests
- yes, the branch stays honest about what remains unsolved for OAuth providers

## Architectural Invariants

These should stay true throughout the branch:

- joining-device bootstrap transport must not depend on `GET /session/info`
- joining-device bootstrap transport must not require an already-initialized NoteToSelf DB/session
- Manager must not absorb duplicate cloud-adapter logic
- the bootstrap transport path must be clearly narrower than ordinary Hub session behavior
- device-local secrets still stay local; the welcome bundle should not become a general credential dump

## Required Descriptor Change

The welcome bundle's `remote_descriptor` must carry enough information for the Hub bootstrap path to locate the NoteToSelf CodSync remote.

For this branch that at minimum means:

- `protocol`
- `url`
- `bucket` (which is protocol specific, and will eventually generalize, but we're limiting the scope to minio/s3)

The current descriptor shape is too thin because the joining device cannot derive the NoteToSelf bucket name before it has fetched NoteToSelf itself.

So this branch should explicitly include `bucket` in the bootstrap descriptor.

## Scope Shape

### In scope

- define the branch around a bootstrap-capable Hub transport path
- add whatever bootstrap-specific Hub API/library surface is needed so Manager can request NoteToSelf fetch without a normal NoteToSelf session
- include `bucket` in the welcome bundle's `remote_descriptor`
- make the authorizing-side NoteToSelf push use real Hub/cloud transport for the MinIO proof path
- add MinIO/S3 micro tests for the joining-device bootstrap flow
- update docs/specs to state the new boundary clearly

### Out of scope

- designing the OAuth bootstrap credential story
- solving provider auth for a brand-new device
- broad Hub session redesign
- background NoteToSelf refresh/discovery UX after bootstrap
- unifying every bootstrap transport under one final abstraction if that adds unnecessary churn

## Recommended Plan Shape

This branch plan should be judged against these questions:

1. Does it make Hub, not Manager, the owner of bootstrap cloud transport?
2. Does it avoid requiring fake/throwaway NoteToSelf identity state on the  joining device?
3. Does it prove the architecture with MinIO/S3 rather than just another local filesystem shortcut?
4. Does it stay honest that OAuth bootstrap remains unsolved?

If the answer to any of those is “no”, the branch has drifted.

## Validation

The branch should not be considered complete unless it proves all of these:

- the welcome bundle carries a `remote_descriptor` with `bucket`
- the authorizing device can publish the relevant NoteToSelf state through a real cloud-shaped path
- the joining device can fetch NoteToSelf through Hub-owned bootstrap transport without a preexisting NoteToSelf session
- the joining device does not need a fake fully initialized NoteToSelf DB before the fetch
- the bootstrap flow works against local MinIO/S3 tests
- unsupported bootstrap providers fail clearly rather than silently falling back to local-only assumptions

## Architectural Proposal: Bootstrap Session

After digging deep into the Hub codebase, here's the key finding:

**The Hub can already start with zero participant state.** `SmallSeaBackend.__init__`
creates `root_dir`, `Logging/`, and an empty `small_sea_collective_local.db`
with session/pending_session tables. No `Participants/` directory needed.

**`_lookup_session` is cheap.** It reads a single row from the Hub's local
session DB. It does NOT touch participant filesystems or NoteToSelf DBs.

**`proxy_cloud_file` for S3 is almost free.** It validates the session token
(cheap DB lookup), checks `team_name == "NoteToSelf"`, then does an anonymous
S3 read. No participant credentials involved.

The bottleneck is session *creation*: `request_session` calls
`_find_participant` (iterates `Participants/` directories, reads NoteToSelf
DBs for nickname matches) and `_resolve_berth` (reads team/app/berth rows
from NoteToSelf DB). These require the full participant filesystem to exist.

### Proposed solution: `create_bootstrap_session`

Add a new method to `SmallSeaBackend`:

```python
def create_bootstrap_session(self) -> bytes:
    """Create a short-lived session for identity bootstrap transport.

    Does not require any participant state to exist. The session is scoped
    to NoteToSelf and can be used with proxy_cloud_file for anonymous-read
    protocols (S3).

    Returns the session token (bytes).
    """
```

This method:
- generates a random session token
- inserts a session row with placeholder values:
  - `participant_id`: a zero UUID or the joining device's device_id
  - `team_name`: `"NoteToSelf"` (so `proxy_cloud_file` accepts it)
  - `app_name`, `berth_id`, `team_id`: placeholder values
  - `client`: `"bootstrap"`
- skips the PIN flow, `_find_participant`, and `_resolve_berth` entirely
- optionally sets a short TTL

Expose it via a new endpoint:

```python
@app.post("/sessions/bootstrap")
async def create_bootstrap_session():
    token = app.state.backend.create_bootstrap_session()
    return {"token": token.hex()}
```

No auth required — same as `/sessions/request`. The session grants minimal
access: only `proxy_cloud_file` for anonymous-read protocols. The joining
device's Manager calls this once, gets a token, and uses
`ExplicitProxyRemote` to fetch NoteToSelf through `/cloud_proxy`.

### Why this works

1. **Hub is the gateway.** Manager talks to its local Hub via HTTP (or
   TestClient), never touches cloud directly.
2. **No fake NoteToSelf state.** The bootstrap session doesn't read from any
   participant directory. It's a real session row in the Hub's DB.
3. **Reuses existing infrastructure.** `ExplicitProxyRemote` already talks to
   `/cloud_proxy`. `proxy_cloud_file` already does anonymous S3 reads. The
   only new code is the session creation shortcut.
4. **Clearly narrower than normal sessions.** The `client: "bootstrap"` marker
   makes bootstrap sessions distinguishable. They can be excluded from the
   watcher, restricted to certain endpoints, and auto-expired.

### Why not sessionless?

An alternative is a new endpoint that doesn't require any session — just
accepts `{protocol, url, bucket, path}` directly. This is simpler but:

- breaks the Hub's consistent auth model (every cloud endpoint requires a
  session today)
- exposes an unauthenticated cloud proxy on localhost, which is a broader
  surface than needed
- makes it harder to audit/rate-limit bootstrap activity

The bootstrap session is slightly more ceremony but stays consistent with the
existing architecture.

### Why not Hub-as-library?

Another alternative is importing `SmallSeaBackend` or the adapter layer
directly into Manager and calling `proxy_cloud_file`-like logic in-process.

This could work but:

- blurs the Hub/Manager separation that the repo maintains
- in production the Hub process is already running — talking to it via HTTP
  is the normal path
- `ExplicitProxyRemote` already implements the CodSync remote interface over
  HTTP — no new code needed on the Manager side
- tests already inject `TestClient` as the HTTP layer, keeping everything
  in-process anyway

So: use the running Hub process in production, use TestClient in tests. Same
code path.

### Joining-side bootstrap flow (concrete)

1. Manager calls `POST /sessions/bootstrap` on local Hub → gets
   `bootstrap_token`
2. Manager creates `ExplicitProxyRemote(bootstrap_token, protocol, url,
   bucket)` using `remote_descriptor` from the welcome bundle
3. CodSync fetches NoteToSelf through `/cloud_proxy` → Hub does anonymous S3
   reads
4. Manager checks out the fetched repo, verifies the welcome bundle signature
   against `user_device.signing_key` (existing logic)
5. Manager initializes the real participant state from the fetched data
6. Bootstrap session can be deleted or left to expire

### Authorizing-side push (concrete)

The authorizing device already has a running Hub and a NoteToSelf session.
`_push_note_to_self_to_local_remote` currently only supports `localfolder`.

For Hub-backed protocols:
1. The authorizing device's Manager gets a NoteToSelf session (already exists
   or auto-approved)
2. Creates `SmallSeaRemote(session_hex)` pointing at its local Hub
3. CodSync pushes NoteToSelf through `POST /cloud_file` → Hub writes to S3

This requires extending `_push_note_to_self_to_local_remote` (or adding a
parallel function) to use `SmallSeaRemote` when the protocol isn't
`localfolder`.

The authorizing-side Hub session creation works normally because the
authorizing device has full participant state.

### CodSync remote selection in `_remote_from_descriptor`

Today:
```python
def _remote_from_descriptor(remote_descriptor):
    if protocol == "localfolder":
        return LocalFolderRemote(url)
    raise NotImplementedError(...)
```

After this branch:
```python
def _remote_from_descriptor(remote_descriptor, *, bootstrap_token=None,
                            hub_url="http://localhost:11437", http_client=None):
    if protocol == "localfolder":
        return LocalFolderRemote(url)
    if protocol == "s3":
        if bootstrap_token is None:
            raise ValueError("Hub-backed protocols require a bootstrap token")
        return ExplicitProxyRemote(
            bootstrap_token, protocol, url, bucket,
            base_url=hub_url, client=http_client,
        )
    raise NotImplementedError(f"Unsupported bootstrap protocol: {protocol}")
```

### What about `proxy_cloud_file` and the NoteToSelf check?

`proxy_cloud_file` currently enforces `team_name == "NoteToSelf"`. Bootstrap
sessions have `team_name = "NoteToSelf"`, so this check passes. No change
needed.

### What about the watcher?

The lifespan loop calls `_register_session_peers` for every session. For
NoteToSelf sessions, `_register_session_peers` already returns early:

```python
if ss_session.team_name == "NoteToSelf":
    return  # NoteToSelf has no peers
```

So bootstrap sessions won't trigger any watcher activity.

## Concrete Change Areas

### `small-sea-hub/backend.py`

- add `create_bootstrap_session()` method
- no changes to `proxy_cloud_file`, `_lookup_session`, or existing session
  logic

### `small-sea-hub/server.py`

- add `POST /sessions/bootstrap` endpoint (no auth required)

### `small-sea-manager/provisioning.py`

- extend `_remote_from_descriptor` to support `ExplicitProxyRemote` for
  Hub-backed protocols
- extend `_push_note_to_self_to_local_remote` (or add parallel) to use
  `SmallSeaRemote` for Hub-backed protocols
- update `authorize_identity_join` to include `bucket` in `remote_descriptor`
- update `bootstrap_existing_identity` to request a bootstrap session and
  pass it through to `_remote_from_descriptor`

### `small-sea-manager/manager.py`

- `bootstrap_existing_identity` (the module-level wrapper) may need to accept
  Hub connection info (port, http_client) so it can request a bootstrap
  session
- `authorize_identity_join` on `TeamManager` may need to push through Hub
  when protocol isn't `localfolder`

### `small-sea-note-to-self/bootstrap.py`

- `WelcomeBundle.remote_descriptor` already accepts arbitrary dicts — just
  include `bucket` when building it

### `small-sea-client/client.py`

- add `request_bootstrap_session()` method that calls
  `POST /sessions/bootstrap`

### Tests

- MinIO integration test: full round-trip through Hub
  - authorizing device pushes NoteToSelf via SmallSeaRemote → Hub → S3
  - joining device fetches via bootstrap session → ExplicitProxyRemote →
    Hub → S3
  - verify signature, second confirmation string
- existing localfolder tests unchanged

## Implementation Order

### Phase 1: Bootstrap session creation

- `SmallSeaBackend.create_bootstrap_session()` + endpoint
- `SmallSeaClient.request_bootstrap_session()`
- unit test: can create bootstrap session on empty Hub, token is valid for
  `_lookup_session`

### Phase 2: Authorizing-side push through Hub

- extend `_push_note_to_self_to_local_remote` for non-localfolder protocols
- the authorizing device uses `SmallSeaRemote` through its local Hub
- test: authorizing device can push NoteToSelf to MinIO through Hub

### Phase 3: Joining-side fetch through Hub

- extend `_remote_from_descriptor` to return `ExplicitProxyRemote` for
  Hub-backed protocols
- update `bootstrap_existing_identity` to request a bootstrap session
- add `bucket` to `remote_descriptor` in `authorize_identity_join`
- test: joining device can fetch NoteToSelf from MinIO through Hub

### Phase 4: End-to-end MinIO test

- full round-trip: create participant → push to MinIO → join request →
  authorize → bootstrap through Hub → verify
- proves the entire Hub-mediated bootstrap path

### Phase 5: Docs

- update specs to describe the bootstrap session concept
- document which protocols are supported for bootstrap (S3 for now)
- document what remains unsolved (OAuth providers)

## Risks

- **Bootstrap session is unauthenticated.** Anyone on localhost can create
  one. Mitigation: bootstrap sessions only grant anonymous-read proxy access,
  which for S3 public buckets is no more than what `curl` could do. The
  session doesn't grant write access or credential access.
- **Placeholder IDs in bootstrap session rows.** These are slightly unusual
  DB state. Mitigation: mark with `client = "bootstrap"` and consider
  auto-cleanup.
- **MinIO test infrastructure.** Mitigation: skip if MinIO not available,
  keep fixture self-contained.
- **`proxy_cloud_file` might grow beyond S3.** For OAuth providers, the
  bootstrap session can't provide credentials. Mitigation: this branch is
  explicitly S3-only. The bootstrap session concept can be extended later
  with credential injection, but that's a separate design.

## Questions To Defer Until The Next Planning Step

These are real design questions, but they belong in the **implementation/refactor planning** step, not this branch-shape step:

- what exact Hub API or library surface should expose bootstrap transport?
- how much of the current cloud-adapter logic should be extracted vs wrapped?
- should the bootstrap path be HTTP-only, in-process-only, or support both?
- should the existing `LocalFolderRemote` bootstrap path be routed through the same new abstraction later, or left alone for now?
- what is the cleanest authorizing-side push path through Hub for the MinIO proof flow?

Those are important, but they are downstream of the branch goal. This draft is intentionally stopping short of solving them.
