# Open Architecture Questions

Decisions that are hard to change once downstream code is written. Work through these roughly in order.

---

## 1. Encryption Layer Shape

The Hub-as-chokepoint architecture exists to enable transparent E2E encryption, but the encryption layer isn't implemented yet. This decision ripples into everything else.

**Questions to answer:**
- What does the Hub encrypt/decrypt, and when? Does it encrypt before writing Cod Sync bundles, or after? Does the Hub hold decrypted data in memory during a session?
- Where do private keys live? Hard disk? OS keychain? Passphrase-protected? This determines the threat model.
- What does cloud storage actually see? If S3 sees only encrypted blobs, the Cod Sync chain format probably needs to be encryption-aware (e.g., metadata vs. payload separation).

**Why it's urgent:** Building out Team Manager, the invitation flow, and Cod Sync consumers before answering these means retrofitting encryption into many call sites.

---

## 2. Hub ↔ Team Manager Database Contract

Explicitly TBD in the Hub spec. Hub needs to read team membership/permissions to make authorization decisions; Team Manager owns writes. This is a hard coupling.

**Questions to answer:**
- One shared SQLite file, or two files with a sync/notification contract?
- What's the read interface — does Hub query the DB directly, or does Team Manager expose a query API?
- Who owns schema migrations, and what happens if Hub and Team Manager are on different versions?

**Why it's urgent:** The Team Manager spec is skeleton-only. This contract unblocks finishing it.

I think one shared SQLite file is the simple and easy answer, but it might have some pitfalls.
Changing team membership, service subscriptions, user devices, certificates, etc (the tasks of the Team Manager) should be relatively low frequency for most people.
I don't expect most people will have the Team Manager app open most of the time.
(Side note: It might make sense to rename this package "Small Sea Manager" or something, since it manages more than teams.)
Having the Hub read the SQLite DB every time it needs to check for a team member's key or something might be a performance problem.
But I think it should be sufficient for the Hub to keep a basic in-memory cache and monitor the file for modifications, flushing its cache whenever the DB is modified.
I think it's fine to assume/enforce that the Hub and Team Manager on a particular device will stay in sync version-wise.
They are the heart of the Small Sea code infrastructure; they should be updated together.


⏺ Good approach — shared SQLite is the right call for the access pattern you're describing. A few targeted follow-ups to pin down the details:
(Answers together below)

  ---
  1. Cache invalidation granularity

  Flushing the whole cache on any DB modification is simple and almost certainly sufficient given the low change frequency. But it's worth stating explicitly, because the alternative (per-table invalidation) adds complexity for marginal
  gain. Proposed rule: Hub flushes its entire in-memory cache on any core.db modification event. Agree?

  ---
  2. What does Hub actually read, and is that set stable?

  Looking at the current code, Hub reads these tables directly:
  - Nickname — participant lookup during session open
  - Team, App, TeamAppStation — membership validation
  - CloudStorage — cloud credentials per session
  - NotificationService — ntfy endpoint

  What's conspicuously absent is any authorization check beyond "is this participant a member of this team/app?" Right now any participant in the right team can do anything. Is that intentional for now, or is there a richer permissions model (read-only vs. admin, per-station ACLs, etc.) that needs to land in the schema before Hub starts caching it?

  This matters because if permissions are coming later, the schema needs a placeholder for them now — otherwise the cache design might not have the right cache keys.

  ---
  3. Hub writes to core.db too

  _add_cloud_location in backend.py writes CloudStorage rows to core.db. So Hub is both a reader and a writer. That's fine, but it means the "Team Manager owns writes" framing isn't quite right — it's more like "Team Manager owns writes to membership/identity tables, Hub owns writes to session/cloud-config tables." Worth being precise about this so the schema has a clear ownership line per table.

  ---
  4. Multi-participant file monitoring

  core.db is per-participant (at Participants/{lid}/NoteToSelf/Sync/core.db). A Hub serving multiple participants would need a file watcher per participant DB. Is that the intended deployment? Or is the single-user-Hub model firm enough that this doesn't matter?

  ---
  The rename question ("Small Sea Manager") is a good instinct but orthogonal — happy to defer it. What's your take on the authorization/permissions question in #2? That one most directly affects the schema.


1. Agree. Maybe make a note somewhere that a more fine-grained cache invalidation policy is possible, but probably overkill
2. The plan for the permissions model is simple, but it would probably be good to start to implement it.
   Each member can have read-only or read-write permissions in each station.
   Locally, this doesn't really mean anything; Small Sea doesn't try to police what people/apps do on their own computers.
   One of the unimplemented resposibilities of the Hub is monitoring for updates from teammates (these could come through a notification service, polling cloud locations, a ping over a live VPN connection).
   The Hub *should* only incorporate changes from teammates for stations that they have read-write permissions for.
3. I think this is a mistake.
   The Team Manager should be in charge of managing subscriptions to Cloud services (i.e. writing those parts of the DB).
   The Hub should just read them to know where/how to access.
   Sessions should be kept in a separate Hub-specific database.
   All other apps (including the Team Manager) should only access sessions through the Hub's API
4. I'm pretty comfortable with the single user per Hub model.
   I guess if there's some way we could get badly painted into a corner, it's worth considering.
   But in today's computing world multiple people using the same computer in a "personal/desktop" kind of way simultaneously is pretty exotic.


⏺ Good answers — they clarify a lot and surface some real structural issues. Three threads worth pulling on:
Answers together below.

  ---
  On #3 (Hub writing to core.db is a mistake): This is a meaningful correction. Today /cloud_locations on the Hub writes CloudStorage rows directly to core.db. Post-correction, only Team Manager does that. But that raises a question about
  the flow:

  Is Team Manager a UI-only app that writes to core.db directly (user configures cloud storage through the UI, no API), or does it need an API surface that other programs call? The distinction matters because:
  - If UI-only: the Hub's /cloud_locations endpoint goes away entirely. Client apps never register cloud storage — users do it manually through the Team Manager UI before using anything else.
  - If API server: Team Manager runs a local server alongside the Hub, and Hub's /cloud_locations becomes a proxy to it.

  The current code implies the Hub-as-API model, but your answer suggests the "Hub just reads" model, which sounds more like the UI-only path. Which is it?

  ---
  On #2 (permissions schema): Read-only vs. read-write per station is clear at the concept level. But TeamAppStation currently has no permissions column — it's just (team_id, app_id). Where does the permission live?

  Options:
  - A role column on TeamAppStation (e.g., "read-only" / "read-write")
  - A separate TeamMember table with (team_id, participant_id, role), referenced from TeamAppStation

  The second option feels more natural (permissions are per person per team, not per station), but the schema doesn't have a TeamMember table yet. What's the right shape?

  Also: when Hub monitors for incoming teammate updates and checks their permission, whose copy of the permissions table is authoritative? Your own local core.db? Or the teammate's? (It should be yours — you decide who you trust — but worth stating explicitly.)

  ---
  On the monitoring mechanism for teammate updates: This is currently entirely absent from the Hub implementation. The Hub only does things when a client app asks. The permissions check you described ("only incorporate changes from teammates with read-write permissions") implies Hub is doing something proactive — detecting that a teammate pushed new content and deciding whether to pull it.

  Is the intended model:
  - Pull-on-demand: Client app asks Hub to fetch; Hub checks permissions and fetches. Hub never does anything unprompted.
  - Background polling: Hub runs a background loop, periodically checks teammates' cloud locations, pulls if there are updates and permissions allow.
  - Notification-driven: Teammate sends a push notification via ntfy/etc; Hub wakes up and fetches.

  This affects whether Hub needs a long-running background task. What's the intention?


- The Team Manager is UI only.
  It writes to the core DB directly.
  No API surface.
  That's right Small Sea clients (other than Team Manager) should care as little as possible about the services under/behind the Hub.
- Adding a TeamMember table sounds right.
  This table (or other linked tables) will also have a bunch of key/cert stuff down the line
- I think the monitoring/trust question is pretty simple:
   - The Hub "pays attention" to changes from teammates who it thinks have read-write permissions.
     Others are discarded/ignored.
     There might be some corner cases around around permission changes (like Alice upgrades Bob to read-write; Bob publishes some changes; Carol sees Bob's changes before seeing the upgrade from Alice).
     But I think these are careful implementation questions, not architecture.
- The Hub should be monitoring for changes using whatever cloud services are available to the team.
  Yes, I expect the Hub to be always on in the background


⏺ Good, several things are now firm. Let me capture what's settled and push on the remaining gaps.

  Settled:
  - Team Manager is UI-only, writes core.db directly. Hub's /cloud_locations endpoint goes away — it's currently wrong.
  - TeamMember table is needed; will eventually carry key/cert material.
  - Hub is always-on, background-monitoring. Uses local permissions copy as authoritative. Permission-change race conditions are implementation details.

  ---
  Follow-up 1: Permissions granularity — per team or per station?

  You said "each member can have read-only or read-write permissions in each station." That's per station, not per team. But TeamMember(team_id, participant_id, role) is per team. If Alice is read-write for shared notes but read-only for
  the shared vault — both stations under the same team — you need a finer-grained structure.

  Two shapes:
  - TeamMember(id, team_id, participant_id) + StationRole(id, team_member_id, station_id, role)
  - Flat: TeamMemberStation(id, team_id, participant_id, station_id, role)

  The two-table option is more normalized and maps cleanly to the "member of team, with different capabilities per station" model. Does that shape match your mental model, or is per-team granularity actually sufficient for the near term?

  ---
  Follow-up 2: Where does a teammate's cloud location live?

  For Hub to monitor a teammate's updates, it needs to know where that teammate publishes. Today core.db has CloudStorage — but that's your own cloud config. Teammate cloud locations presumably arrive via the invitation flow and need to be stored somewhere.

  This feels like it belongs on TeamMember (or linked to it) — something like a cloud_location field pointing to the teammate's public bucket/path. Is that right? And is a single cloud location per teammate sufficient, or could a teammate publish to multiple locations (e.g., primary S3 + a Dropbox fallback)?

  ---
  Follow-up 3: Who does the actual cloud read when monitoring teammates?

  The "Hub owns all cloud interaction" decision from Section 4 was framed around a single user's writes. But monitoring a teammate means reading from their cloud storage. That storage is configured with your credentials (if it's a shared bucket) or is publicly readable (if it's the globally-public model you described for Cod Sync).

  If teammate data is globally readable by design (encrypted, so public exposure is fine), then Hub can read it without special credentials — it just needs the URL. That's clean. But it means the monitoring loop is: for each teammate, fetch their latest-link.yaml from their known URL, compare to last seen, pull if newer, check permissions before incorporating.

  Does that match the intended model? And if so, does the Hub maintain a "last seen link UID" per teammate per station, so it knows what's new?


- The two table schema makes sense to me
- Yes, making cloud locations belong to a team member sounds right.
   There's a tiny bit of subtlety here for the Team Manager UI to deal with; probably people will often want to use the same cloud across multiple teams/clouds.
   But I think that can can be kicked down the road.
- The Hub does all the actual cloud reading.
   Right, the data is public to the world; privacy enforced with encryption managed by Small Sea.
   It's a good question about managing the monitoring.
   Maybe the Hub should expose a local API with which apps can request monitoring of changes to particular locations?
   I don't want to hard-code too many assumptions into the Hub.


---

## 3. Session Lifecycle & Approval Flow

Sessions are the primary API surface every client app uses. The Hub spec says this is partially TBD.

**Questions to answer:**
- Who approves a session request — the Hub, the Team Manager app, or the user interactively?
- How is a session scoped to a Station? Can one session span multiple stations?
- What triggers expiry — time, user logout, device removal?

**Why it's urgent:** The `small-sea-client` library wraps sessions, so the session shape determines the entire client UX. Getting this wrong breaks all downstream client code.

---

## 4. Cod Sync Chain Format Stability

Any data stored in S3 using the current chain-of-deltas format becomes a migration problem if the format changes later.

### Settled Decisions

These questions were worked through in detail and are now captured in the [Cod Sync format spec](../packages/cod-sync/Documentation/format-spec.md):

- **Concurrency control**: CAS (compare-and-swap) via conditional writes on `latest-link.yaml`. Failed CAS means pull, merge, retry. Implemented in the Hub's storage adapters and threaded through `SmallSeaRemote` and `LocalFolderRemote`.
- **Versioning**: Per-link semver in `supp_data.cod_version`. Major bump = breaking (reader refuses), minor/patch = additive. Version numbers are monotonically non-decreasing forward through the chain.
- **Encryption**: Link blobs and git bundles encrypted as separate files (allows chain traversal without downloading full bundles). Cipher and key exchange TBD.
- **GC / compaction**: Chain compaction (collapse to fresh initial-snapshot) handles both garbage collection and format migration. Any user with write access can trigger it.
- **Hub owns cloud interaction**: S3Remote to be eliminated; all cloud access goes through the Hub.

### Remaining Open Items

- **S3Remote elimination**: Requires reworking the invitation flow. Inviter's cloud data is assumed globally readable (security comes from E2E encryption, not access control). Invitation tokens may include time-limited read paths.
- **Encryption details**: Cipher selection, key exchange protocol, and the bootstrapping flow for new members joining a chain are all TBD.

**Why it's urgent:** Every Cod Sync consumer (Team Manager, shared-file-vault, future apps) inherits this format.


---

## 5. Identity Model: NoteToSelf Station & Multi-Device

The `NoteToSelf-SmallSeaCore` station holds personal keys and device info. The open question "can a single Hub serve multiple users?" is related.

**Questions to answer:**
- Is identity device-local or portable? Two devices = two identities, or one?
- How does an X3DH prekey bundle get published so that people inviting you can discover it? Is it in your public S3, and what signs it?
- What happens to encrypted data if a device is lost — is there a key backup/recovery story?

**Why it's urgent:** The invitation flow and key rotation logic both depend on the identity model. It can be stubbed longer than the others but shouldn't be deferred past the point where invitations are fully wired up.

---

## Suggested Order

1. Hub ↔ Team Manager DB contract — concrete, scoped, unblocks Team Manager spec
2. Session lifecycle — write it out in Hub spec before writing more client code
3. Encryption layer interface — even a rough API sketch (encrypt/decrypt boundary, key storage stub) protects against having to retrofit it everywhere
4. ~~Cod Sync chain format~~ — mostly resolved; see [format spec](../packages/cod-sync/Documentation/format-spec.md). Remaining: encryption details, S3Remote elimination
5. Identity model — most complex; can be stubbed a while longer
