# Instructive Protocol Analysis

This doc contains notes about protocols that are instructive for Small Sea in one way or another.
Whether Small Sea should follow another protocol's precedent is entirely case-by-case.
The goal here is simply to leverage hard-won wisdom from other projects.

These are design summaries, not substitutes for the linked specifications.
Claims about key custody and device metadata have been checked against the cited protocol documents and current public wire formats; deployment and product-history claims remain intentionally high-level.

## What This Doc Is For

Small Sea's core shape is settled enough that the remaining work is detail.
Details are where field-tested protocols are worth more than fresh invention.
Each entry answers three questions:

1. What did this protocol get right, and at what scale?
2. Which mechanism is worth borrowing?
3. Where does it contrast with Small Sea, and what did its choice cost it?

The third question carries the most weight.
Small Sea deliberately gives up things many of these protocols rely on — an authoritative service operator, a global identity per person, a single agreed ordering — so the interesting content is usually what those protocols bought with what Small Sea has refused.

The doc is split into two parts because "instructive" and "successful" are not the same property.
Part I covers protocols that ran at scale, where the costs are observed rather than predicted.
Part II covers designs valued for their vocabulary, some of which were never widely deployed.
SPKI/SDSI is the closest conceptual ancestor to Wrasse Trust's cert model and also a commercial non-event; both facts are worth knowing.

## What Lives Elsewhere

This doc is the one-stop shop for *mechanism* comparison.
Three neighbouring bodies of comparative writing deliberately stayed where they are:

- [`related-work.md`](related-work.md) asks a positioning question — who would claim Small Sea's ground, and what is the honest differentiation?
  Jazz, Earthstar, Radicle, and Spritely live there.
  An entry that only establishes that a project exists belongs there, not here.
- [`packages/small-sea-live/architecture.md`](../packages/small-sea-live/architecture.md) §Prior Art To Study compares realtime-transport products (Ably, NATS, MQTT, Yjs Awareness, Automerge Repo, Liveblocks, libp2p pubsub).
  Those comparisons are arguments about Small Sea Live's scope and are only meaningful inside that decision.
- [`packages/the-hedgerow/README.md`](../packages/the-hedgerow/README.md) §Comparison Points covers SSB, Nostr, ActivityPub, AT Protocol, and Briar as social models.
  Its borrow/reject lists are design decisions for that app rather than a survey.

The sorting test, if a new comparison needs placing: **would this comparison still be true if the feature were cancelled?**
SPKI/SDSI's local-names idea holds whether or not linked teams ships, so it belongs here.
Liveblocks-as-market-signal only means something inside the Small Sea Live scope argument, so it belongs there.

## The Recurring Axis

Nearly every entry differs from Small Sea on the same three points, so they are stated once here rather than repeated:

- **Identity scope.** The deployed messaging systems anchor a person to one global identifier — a phone number, an account, or a long-term key hierarchy.
  Small Sea gives a person a fresh per-team teammate UUID and no global identity in the protocol at all.
- **An authoritative service.** Most deployed systems depend on a service that is not trusted with plaintext but *is* authoritative for some combination of device discovery, sequencing, delivery, and rate-limiting.
  Small Sea has Hubs and storage providers, but neither may become the authority that decides which signed team history is canonical.
- **Membership as agreed state.** Most group systems produce one answer to "who is in this group right now."
  Small Sea's Constitution deliberately declines to, leaving membership to extensions and local policy over a DAG that preserves concurrency.

## Cross-Cutting Finding: Device Keys and Certification Roots Are Separate Axes

Consolidating these summaries surfaced a distinction that had been blurred across three separate Small Sea documents.
"Does the new device generate its own key?" is not enough to classify a provisioning design.
The separate question is whether some per-person certification or decryption secret also has to reach that device.

The three examples make a spectrum:

- **Signal copies account identity secrets.** Its encrypted provisioning message carries the ACI and PNI identity private keys to the linked device.
  Its messaging sessions are still per-device, but the identity-key layer is shared.
- **Keybase keeps device keys local but distributes a Per-User Key.** A device generates its own device key, then receives the current PUK seed encrypted to that key.
  Revoking a device rotates the PUK and re-wraps the new seed for the remaining devices.
- **Matrix keeps device keys local but has portable per-user cross-signing keys.** Its master, user-signing, and self-signing private keys may be placed in Secret Storage or shared with another device.
  The self-signing key, rather than an existing device key directly, signs the new device key in the normal cross-signing path.

Small Sea takes a stricter fourth position: each device generates and retains its own per-team operational key, and there is no persistent per-teammate certification key to copy, wrap, escrow, or share.
An enrolled sibling device links the fresh key by issuing a `device_link` cert, while prepared recovery is a separate capability and ceremony rather than a quiet copy of an operational identity root.

Matrix is therefore the closest ancestor for the *shape of the signed device graph*, not for Small Sea's key-custody model.
Signal remains the right citation for the separate claim that multi-device messaging means pairwise sessions to each destination device rather than one identity-level channel.

References:

- Signal's current public [`ProvisionMessage`](https://github.com/signalapp/Signal-Android/blob/main/lib/libsignal-service/src/main/protowire/Provisioning.proto) includes both ACI and PNI identity private keys.
- Matrix specifies that [cross-signing private keys may be stored or shared](https://spec.matrix.org/latest/client-server-api/#cross-signing).
- Keybase documents [device-local device keys and a PUK encrypted for every active device](https://book.keybase.io/docs/teams/puk).

## Cross-Cutting Finding: A Device Record Is Not One Kind of State

The protocols also reinforce the placement rule Small Sea's device-metadata work is applying.
A convenient UI object called "device" does not justify one replicated protocol object containing everything known about it.
Classify each field by who can assert it, who needs it, how quickly it changes, and whether trust depends on it:

- **Cryptographic standing** — the device key and the signed history that associates it with a teammate — is durable team state.
  `team_device` should remain a projection of admission and `device_link` history, not a bag of mutable attributes.
- **Participant-owned description** — a label used to distinguish one's own devices — belongs in shared NoteToSelf state.
  It need not be team-visible or trust-bearing.
- **Observed liveness** — last-seen, reachability, or recent sync success — belongs on the observing device or Hub.
  Different observers legitimately have different answers, so there is no team value to converge on.
- **Operational discovery material** — X3DH prekey bundles, capabilities, or future key-package equivalents — needs its own signed, replaceable lifecycle.
  It is neither a friendly label nor proof that the device still has standing.
- **Routing** — where a teammate's berth can be fetched — is teammate-and-berth state.
  It is not an intrinsic property of the signing device that happened to announce it.

Matrix makes the separation unusually visible: its [device-key response](https://spec.matrix.org/latest/client-server-api/#post_matrixclientv3keysquery) puts the user-set display name in an explicitly `unsigned` object, while its [account device API](https://spec.matrix.org/latest/client-server-api/#get_matrixclientv3devices) reports homeserver-observed `last_seen_ts` and IP address.
Keybase made the opposite privacy trade for labels: [device names are signed into its public chain, visible forever, and cannot be changed](https://book.keybase.io/account#adding-a-device).
Signal stores an [encrypted device name and service-observed last-seen state](https://github.com/signalapp/Signal-Server/blob/main/service/src/main/java/org/whispersystems/textsecuregcm/controllers/AccountController.java) in its account service rather than in the Signal Protocol identity key.
In none of the three is a device label itself consulted to decide the key's cryptographic standing.

---

# Part I — Deployed at Scale

## 1. Signal — Sesame, Groups v2, and Device Linking

**Success.** Consumer-scale deployment and sustained public scrutiny.
The most important thing Signal proved is not cryptographic: a protocol with real key management can be usable by people who do not know they are using it.

**Mechanism worth borrowing: Sesame.**
Sesame is Signal's answer to the exact problem `device_link` addresses — one account, many devices, each with its own session state, encryption per-device rather than per-person.
Sending to a person means enumerating that person's current device set and encrypting separately to each.
Sesame assumes the service has the current user/device records.
When a send presents a stale device set, the service rejects it with the old and new device IDs, and the sender repairs its cached records before retrying.
Small Sea can borrow the bounded repair loop and the distinction between active and stale session state, but not the authoritative roster that tells Signal's client what to repair toward.
Small Sea has to derive device standing from signed admission and `device_link` history; a failed delivery can trigger refresh but cannot declare a device added or removed.

**Mechanism worth borrowing: Groups v2 anonymous credentials.**
Signal's group system lets the server store encrypted group state and validate anonymous membership credentials without seeing the ordinary account identifier making a group operation.
That does not hide all service metadata, but it separates "may perform this group operation" from "which account is this?"
This is a real answer to a question Small Sea will face: how much can an untrusted intermediary usefully enforce?

**Device linking, and the correction it forces.**
The new device displays a QR code, the existing registered phone scans it,
and the existing device transfers account identity key material over the resulting encrypted provisioning channel.
The UX is excellent and worth copying.
The key-custody model is not what Small Sea wants — see the cross-cutting finding above.
Signal usernames reduce phone-number disclosure, but account registration remains phone-number anchored.

**Contrast.**
Signal's identity anchor is an account registered with a phone number, and its group state lives on a service providing a single ordering of group changes.
Both are exactly what Small Sea refuses.
The server also does the anti-abuse work — rate limiting, spam, flooding — that the Constitution has to push onto local resource limits.

**What it cost them.**
Reducing phone-number exposure without replacing the account anchor required a second naming layer and substantial product work.
Server-mediated device and group state also makes service availability part of correct operation.
Long-lived signed group history is client/account product data in Signal, not an independently portable protocol artifact as the team repository is in Small Sea.

See also [`packages/cuttlefish/README.md`](../packages/cuttlefish/README.md) for the Signal-style session layering Small Sea actually adopts.

---

## 2. MLS (RFC 9420)

<https://www.rfc-editor.org/rfc/rfc9420.html>

**Success.** A standards-track group key agreement designed for groups from two to thousands, with efficient membership changes, forward secrecy, and post-compromise security, and now [deployed across Wire's messaging products](https://support.wire.com/hc/en-us/articles/12434725011485-Messaging-Layer-Security-MLS).
It is the most rigorous public treatment of "membership change as a cryptographic operation."

**Mechanism worth borrowing: proposal/commit separation.**
MLS splits a membership change into a *proposal* (add, remove, update — describing intent) and a *commit* (an event adopting a set of proposals and advancing the group to a new epoch).
Members may propose changes under application policy; the commit is the moment of cryptographic effect.
Small Sea's admission flow has repeatedly circled this shape without naming it, and the vocabulary is worth adopting even where the ratchet-tree machinery is not.

**Mechanism worth borrowing: epochs.**
A monotonically increasing epoch number gives every party an unambiguous label for "which version of this group am I talking about."
Small Sea has no total order to hang an epoch on, but the notion that key material advances in labeled steps, and that stale-epoch messages are detectable rather than silently wrong, is worth keeping.

**Caution: MLS is not a governance system.**
It answers which authenticated clients hold the current group key, while leaving credential validation and additional membership policy to the application and its Authentication Service.
Any Small Sea use of MLS-like machinery still has to define how signed Constitution history authorizes the change that the ratchet then executes.
This caution matters most for eventual read-sharing across changing membership, including across linked teams.

**Mechanism worth borrowing: narrow, lifecycle-bound discovery objects.**
An MLS KeyPackage carries the signed cryptographic identity, capabilities, and fresh HPKE material needed to add one client.
KeyPackages are intended for one use and should be removed after consumption.
That is a useful model for Small Sea's `device_prekey_bundle`: publish the minimum operational material required to initiate, give it explicit replacement or consumption semantics, and do not turn it into the device's label, liveness record, or standing certificate.

**Contrast — and the sharpest one in this doc.**
MLS requires a *linear* sequence of commits.
Applications must prevent conflicting commits for one epoch or define which one becomes canonical; concurrent commits are not merged.
A Delivery Service often carries and sequences the messages, but RFC 9420 does not require that service itself to be the conflict adjudicator.
Small Sea's core does the opposite on purpose: two events naming the same parent are ordinary siblings, and no timestamp or arrival order picks a winner.

**What adopting it would cost Small Sea.**
The unavoidable cost is canonicalization, not necessarily a bespoke service: the application must choose one next cryptographic state and promptly discard losing forks.
MLS obtains post-compromise security when successful commits inject fresh entropy and advance that state; linear epochs alone do not provide it.
Small Sea can use MLS vocabulary or even MLS inside a messaging extension, but it cannot treat the MLS epoch sequence as the Team Constitution without abandoning the core's concurrency rule.

---

## 3. Matrix — Event DAG, State Resolution, Cross-Signing

**Success.** Matrix is a rare widely deployed system whose room state is a signed, hash-linked DAG with membership carried as events inside it, tolerating partition and concurrent writes across independently operated servers.
Structurally it is the closest living relative to the Team Constitution.

**Mechanism worth borrowing: auth events.**
Each Matrix event names not only its DAG parents but the specific earlier events that authorize it — the sender's membership event, the power-levels event in force, the join-rules event.
Authorization is checked against *those named events*, not against a mutable roster.
This is a concrete answer to a question Small Sea's extension layer must answer, and it preserves the core's insistence that authority is evaluated against ancestry rather than a table.

**Mechanism worth borrowing: cross-signing.**
A device generates its own device keys.
Matrix then uses a per-user self-signing key to sign those device keys and a separate user-signing key to sign other users' master keys; both descend from a per-user master signing key.
Verification by QR scan or emoji comparison establishes trust in that master key rather than requiring every pair of devices to be compared forever.
This is the closest existing ancestor to the *graph shape* of a `device_link` cert.
It is not the same custody model: Matrix explicitly permits its per-user cross-signing private keys to be stored in Secret Storage or shared with another device.

**Mechanism worth borrowing: trust-bearing data is narrower than the device UI.**
Matrix's signed device-key object contains the device ID, algorithms, public keys, and signatures.
The user-set device display name is returned alongside it in an explicitly `unsigned` object.
Separately, the account device API exposes a homeserver-maintained display name, last-seen time, and last-seen IP to the account owner.
This is strong precedent for Small Sea's split: a participant-owned label in NoteToSelf, observer-local liveness in the Hub, and neither field in `team_device` or the `device_link` trust statement.

**Mechanism worth noting: restricted rooms.**
Matrix has working machinery where membership in one room can satisfy a join condition in another.
That is one plausible linked-teams purpose: "membership over there satisfies a condition over here."
The caution is complexity — worth learning the shape without inheriting full federated room-state authorization.
See [`linked-teams.md`](linked-teams.md) for how this bears on bridges.
Reference: <https://spec.matrix.org/latest/client-server-api/#restricted-rooms>

**Contrast.**
Matrix ultimately does resolve concurrent state into one answer, currently via state resolution v2.1 for room version 12, because a chat room must display one member list.
Small Sea's core declines to, and pushes the question to extensions and local policy.
Matrix rooms also live on federated homeservers, which are bespoke services users mostly do not run.

**What it cost them.**
State resolution is genuinely difficult.
Matrix replaced v1 with v2, then introduced v2.1 in 2025 after two high-severity protocol vulnerabilities involving state resets — resolution outcomes that could restore earlier membership or access-control state without a corresponding revocation event.
This is the strongest available evidence for the Constitution's position that a protocol core should not manufacture a winner.
It is also a warning: Small Sea does not escape the problem by declining to solve it in the core.
The difficulty moves to the extension, where it will be solved by fewer people with less review.

Reference: [Matrix Project Hydra and state resolution v2.1](https://matrix.org/blog/2025/08/project-hydra-improving-state-res/).

---

## 4. Keybase — Sigchains and Teams

<https://book.keybase.io/docs/teams/sigchain>

**Success.** Keybase demonstrated a per-user append-only signed chain, with devices added and revoked by signed links, understood well enough by ordinary users that device revocation was a routine operation.
Its teams feature extended the same shape to team operations, subteams, membership, and key rotation.

**Mechanism worth borrowing: the sigchain shape itself.**
A per-subject chain of typed, signed links where each link names its predecessor is close to what a Small Sea per-team device set is.
The typed-link vocabulary — this link adds a device, this one revokes one, this one rotates a key, this one makes a claim — maps well onto the Constitution's opaque-payload-plus-extension-type design.
The general lesson is that team membership and key-management facts can be represented as inspectable signed history rather than as server state.

**Mechanism worth borrowing: transparency anchoring.**
Keybase periodically published a Merkle root committing to all sigchain state, so a user could detect a server showing them a different history than it showed others.
Small Sea's core explicitly cannot prevent equivocation by an authentic key; a Keybase-style anchoring extension is one of the few available responses.

**Device provisioning.**
An existing device — or a paper key — provisions a new one by adding a signed link to the sigchain declaring it.
The new device generates its own device key and receives the current Per-User Key seed encrypted to that device key.
The paper-key path is directly relevant to recovery ceremony design.
The PUK provides signing, encryption, and symmetric secrets shared across the user's devices; that is the layered model Small Sea abandoned.

**Device-metadata warning.**
Keybase signs a device name and type into its public sigchain.
Its user documentation accordingly warns that device names are public and cannot be changed, while revoked devices remain visible.
That permanence helps people audit a global public account, but it is the wrong default for Small Sea labels: the trust graph needs the key and its standing, while a nickname such as an employer's laptop name is participant-owned PII.

**Contrast.**
Keybase's central premise is one global identity per person, cryptographically bound to public social-media accounts — the direct inverse of per-team-scoped identity.
Its subteam hierarchy is a further caution: hierarchy quickly becomes organization machinery, and Small Sea wants signed team relationships without hierarchy as the default answer.

**What it cost them.**
Keybase's auditable device set is inseparable from one public global account and from Keybase's Merkle-tree service.
Even where clients cache and verify the chain, the namespace and global tree remain service infrastructure rather than a team-owned artifact.
That is the argument for the Constitution's insistence that the team event DAG, not an operator's global account database, is the protocol artifact.

---

## 5. Certificate Transparency

<https://www.rfc-editor.org/rfc/rfc9162.html>

**Success.** CT changed the security posture of the entire Web PKI without adding any consensus mechanism and without requiring CAs to agree with each other about anything.
It is the most successful deployment of "we cannot prevent this, so we will make it undeniable."

**Mechanism worth borrowing: detection instead of prevention.**
CT does not stop a CA from issuing a bad certificate.
It makes issuance publicly logged and an unlogged certificate unusable in practice, so misissuance is discovered rather than prevented.
The Constitution's list of things the core cannot provide reads like a list of candidates for this treatment — particularly equivocation and non-disclosure of known events.

**Mechanism worth borrowing: gossip against split views.**
A log operator can in principle show different append-only views to different clients.
CT makes conflicting signed tree heads comparable, but RFC 9162 explicitly leaves the gossip mechanism undefined.
Small Sea's core already states that no peer can prove it disclosed everything it knew; a transparency extension would likewise need to define both the compact signed view being compared and the actual teammate-to-teammate carrier.
Saying "use gossip" without those two pieces does not add a guarantee.

**Contrast.**
CT's subjects are public by design — the mechanism depends on universal readability of the log.
Small Sea team history is private to the team, so any transparency extension must work with a small, closed, mutually-known audit set.
Direct comparison may be easier in a small team, but independent coverage is weaker: there are no domain owners, public monitors, or browser vendors checking on the team's behalf.

**What it cost them.**
CT took roughly a decade to become mandatory, needed browser vendors with the market power to enforce logging, and imposed real operational cost on log operators.
Small Sea has no equivalent enforcement lever, so a transparency extension has to be worth adopting on its own merits or it will not be adopted.

---

# Part II — Design Vocabularies

These are valued for the concepts they named.
Deployment scale varies from modest to essentially zero, so "what it cost them" is replaced by the more useful question of why they did not win.

## 6. SPKI/SDSI

<https://www.rfc-editor.org/rfc/rfc2693.html>

**What it is.** A 1990s design for decentralized authorization: signed delegation, *local names*, threshold subjects, validity conditions, and no global naming authority.
The closest conceptual ancestor to Wrasse Trust's typed cert model.

**Mechanism worth borrowing: local names.**
A name is always someone's name for something, resolved through the namespace of the principal who issued it.
This is a precise fit for two Small Sea positions that were arrived at independently.
Per-team identity is a local-names claim: there is no global "Alice," only this team's Alice.
And for linked teams, Team A's name for Team B is really Team A's name for the people it reaches Team B through.

**Mechanism worth borrowing: authorization without identity.**
SPKI's central insight is that the useful question is "may this key do this thing," not "who is the human behind this key."
That separation is exactly the Constitution's split between proving a key signed an event and deciding whether that key was entitled to act.

**Mechanism worth borrowing: typed authorization.**
Delegation edges carry explicit meaning rather than a single undifferentiated "I vouch for this."
The typed-cert argument in `README-brain-storming.md` descends from this, with PGP's untyped web of trust as the counterexample.

**Why it did not win.**
RFC 2693 remained Experimental and never acquired the deployment ecosystem that X.509 already had in certificate authorities, browser vendors, and commercial tooling.
The lesson is not that local names or typed delegation were wrong — related ideas reappeared in object-capability systems and typed signed links — but that a trust vocabulary needs a carrier and an adoption path.
Small Sea's carrier is the team repository, which is a real advantage over SPKI's position.

## 7. TUF (The Update Framework)

<https://theupdateframework.github.io/specification/latest/>

**What it is.** A framework for securing software update systems, deployed in PyPI, Docker/Notary, and (as Uptane) automotive over-the-air updates.
Its subject is package distribution, but its machinery is general signed-delegation infrastructure.

**Mechanism worth borrowing: offline roots with delegated online roles.**
The root role delegates to narrower roles, and its keys are expected to be kept offline; routine timestamp and snapshot work can use keys with less authority.
This maps onto a distinction Small Sea keeps circling: rarely-used recovery capability versus routine device signing.

**Mechanism worth borrowing: thresholds.**
Roles require *k* of *n* signatures, so no single key compromise is decisive.
This is the most concrete available model for future team governance — committee admission, recovery authorization, exclusion — and it is worth reaching for before inventing a threshold scheme.

**Mechanism worth borrowing: rollback and freeze are different failures.**
TUF uses monotonically increasing metadata versions to reject state older than a client has already trusted, and expiration to detect a repository frozen at the newest version the attacker is willing to serve.
Small Sea's core deliberately does not assign causal authority to wall clocks, so expiry cannot be adopted as a Constitution rule.
The version side still matters for mutable operational publications: a prekey bundle or routing announcement needs an explicit replacement relationship, not an `announced_at` timestamp that silently chooses a winner.

**Device-metadata lesson.**
TUF does not put root trust, delegated signing authority, freshness, and target description into one undifferentiated key record.
Small Sea should keep the analogous lifecycles separate: `device_link` establishes standing, a prekey publication enables session initiation, and a berth announcement supplies routing.
One may become stale or be replaced without rewriting the meaning of the others.

**Why it is less cited than it should be.**
TUF solved a problem most people did not know they had, in a domain (package managers) that treats security as infrastructure rather than product.
Its threshold and delegation design is more mature than most decentralized-identity work and is routinely reinvented worse.

## 8. Macaroons

<https://research.google/pubs/macaroons-cookies-with-contextual-caveats-for-decentralized-authorization-in-the-cloud/>

**What it is.** Bearer credentials that anyone holding them can *attenuate* by appending caveats — scope, expiry, additional conditions — without contacting the issuer.
Attenuation is enforced by chained HMAC, so a caveat can be added but never removed.

**Mechanism worth borrowing: attenuation without round-trips.**
Handing on less authority than you hold, offline, is precisely the shape a bridge or invitation needs.
An inviter should be able to pass a narrowed capability without a server adjudicating the narrowing.

**Contrast.**
Macaroons are bearer tokens: possession is authority, and there is no inherent record of who exercised it.
Small Sea's durable authority should be signed Constitution history, which is auditable and attributable.
The likely synthesis is macaroon-style attenuation for short-lived operational grants, with signed events for anything the team needs to inspect later.

**Why the caution matters.**
Bearer capabilities are socially blunt.
"Whoever holds this string may act" does not survive contact with a team that needs to answer *who did that, and were they entitled to*.

## 9. Tahoe-LAFS

<https://tahoe-lafs.readthedocs.io/en/latest/architecture.html>

**What it is.** A capability-based distributed storage system where read and write authority are represented by cryptographic capability strings rather than accounts, and storage servers are untrusted.

**Mechanism worth borrowing: untrusted storage as a design premise.**
Servers hold ciphertext and cannot recover file contents, but they still observe storage indices, object sizes, timing, and access patterns.
This is structurally what Small Sea does with generic cloud storage, and Tahoe-LAFS worked through the consequences — erasure coding, repair, verification — earlier and more thoroughly than most.

**Mechanism worth borrowing: read/write capability separation.**
A read-cap derives from a write-cap, so delegating read access never risks write access.
Useful for any eventual read-sharing across a team boundary.

**Contrast.**
Same as Macaroons: bearer capabilities carry no account of who used them.
Small Sea likely needs signed constitutional grants and auditable history, not only possession of a string.

**Why it stayed small.**
Tahoe-LAFS exposes a capability and grid-operating model far outside mainstream storage UX.
Small Sea's "regular folks" stance means the Hub and Manager have to absorb comparable machinery without asking users to manipulate capability strings or understand a storage grid.
Failing at that product translation is a real risk even when the underlying protocol is sound.

---

## Queued for Later Passes

Named now so the selection is deliberate rather than accidental:

- **PGP / the web of trust.** The instructive failure.
  Transitive trust and a global keyring, why per-team scoping is a response to it rather than a novelty, and why untyped certifications are difficult to reason about.
  The typed-cert argument that descends from this is already made in `README-brain-storming.md`.
- **SSH host keys and TOFU.** Possibly the most successful trust protocol ever deployed, by being the least ambitious.
  Directly relevant to the two-manual-exchange invitation flow and to the planned TOFU trust policy.
- **Apple iCloud Keychain and Contact Key Verification.** Consumer-scale device sets and escrow-based recovery with hardware-enforced attempt limits.
  The closest existing answer to "recovery for regular people who will not hold a paper key."
- **WhatsApp multi-device.** The largest-scale deployment of Signal-style per-device sessions, with a companion-device model worth contrasting against a flat device set.
- **Git itself.** Small Sea already depends on it, but its social conventions — what a merge means, why authorship is advisory, how forks stay legitimate — are underexamined as a protocol.
- **Sigstore.** Short-lived keys plus a transparency log, as an alternative to long-lived key custody.
- **Nostr.** Treated in `packages/the-hedgerow/README.md` as a social contrast case; the relay-as-dumb-storage model deserves a mechanism-level look here given Small Sea's use of generic cloud storage.
- **AT Protocol.** Also treated in the Hedgerow README socially.
  The mechanism worth a look here is its separation of personal data repositories from identity, routing, indexing, and application views — a reference for separating durable signed data from discovery and service roles.
- **Secure Scuttlebutt.** Same split: the Hedgerow covers the social model, while *subjective replication* — replicating what your social graph reaches rather than what a server holds — is a general mechanism worth its own entry.
- **Autocrypt and Delta Chat.** Opportunistic key distribution riding an existing generic transport, which is structurally what Small Sea does with cloud storage.

## Adding an Entry

Keep entries short enough to read in one sitting.
An entry earns its place by supplying a mechanism Small Sea can adopt, or a cost Small Sea would otherwise have to discover by paying it.
An entry that only establishes that a protocol exists belongs in `related-work.md` or nowhere.
Apply the sorting test in *What Lives Elsewhere* before adding comparative writing to a package doc.
