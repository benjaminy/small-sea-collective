# Session capability models: prior art for Small Sea

Small Sea can grant cryptographic operations through sessions without teaching the framework what each app does.
The useful precedent separates three decisions: who may request an operation, what the key may do, and what a recipient should accept.

At commit `16795bd`, session approval records an app and berth, but no requested capability set.
`backend.py::sign_commit` hardcodes Files’ purposes and checks the signed context against the session.
`work_context.py` and `berth_authority.py` also restrict purposes to a framework-owned list.
These are distinct places where app knowledge enters the framework.

## OAuth 2.0 scopes

The client requests scopes when seeking authorization.
The authorization server obtains the resource owner’s authorization, sometimes through a prompt and sometimes through an existing grant or policy.
OAuth does not prescribe a prompt on every request.

Scopes describe access to resources, not cryptographic key bindings.
An access token represents granted authority; ordinary bearer tokens are not inherently bound to a device or presenting process.
The resource server checks the token and required scope on each protected request.
OAuth defines scope syntax and negotiation, while the service defines scope meaning.
It does not interpret the client’s workflow.
See RFC 6749, §§3.3, 4.1 and 7, [*The OAuth 2.0 Authorization Framework*](https://www.rfc-editor.org/rfc/rfc6749).

Incremental authorization lets the client request additional access when a feature needs it.
Google’s implementation can combine previously granted scopes with newly requested scopes.
This is provider behavior, not a universal incremental-authorization mechanism specified by RFC 6749.
See [*Using OAuth 2.0 for Web Server Applications*, “Incremental authorization”](https://developers.google.com/identity/protocols/oauth2/web-server#incrementalAuth).

The design risks are broad scopes, accumulated permissions, and repeated prompts that users stop reading.
For Small Sea, requesting an additional capability should not silently enlarge an existing session.

## Android Keystore

An app requests key creation or import and specifies purposes such as signing, encryption, or decryption.
Creating an app-private key does not itself require a user permission prompt.
The app can require user authentication for every operation or permit use during a configured interval after authentication.

Keys normally belong to the app’s Android UID within an Android user.
Hardware-backed keys also bind their secret material to the device’s secure hardware.
Keystore checks caller access; the keystore implementation and, where supported, secure hardware enforce algorithms, purposes, and authentication requirements.
Not every restriction has hardware enforcement.

The platform understands cryptographic operations, not whether signed bytes represent an invoice or a file merge.
A compromised app may still invoke an authorized key even when it cannot extract it.
Hardware availability and authentication-related key invalidation also complicate application behavior.
See [*Android Keystore system*](https://developer.android.com/privacy-and-security/keystore).

An app can request key attestation.
A remote verifier checks the certificate chain and reported properties to assess key protection and authorizations.
Attestation does not prove that the human approved the meaning of particular signed bytes.
See [*Verify hardware-backed key pairs with key attestation*](https://developer.android.com/privacy-and-security/security-key-attestation).

## WebAuthn and passkeys

A relying party requests credential creation during registration and an assertion during authentication.
The browser and authenticator mediate user participation.
User presence establishes interaction; user verification checks the user through a PIN, biometric, or another supported method.
Neither establishes informed approval of an application transaction.
Exact interaction and authentication frequency depend on the ceremony, requested verification policy, and authenticator.

A credential is scoped to a relying-party ID and associated with an account.
“Per-origin keys” is imprecise: an RP ID can cover several origins, and related-origin mechanisms allow further controlled sharing.
Synced passkeys also need not remain on one device.

The browser checks whether the caller may use the RP ID.
The authenticator uses the scoped credential.
The relying party checks the signature, challenge, origin, RP ID hash, and required presence or verification flags.

WebAuthn signs a defined authentication structure, not arbitrary application documents.
It does not interpret the business meaning of the challenge.
Overbroad origin acceptance weakens isolation, and passkey synchronization changes assumptions about device identity.
See [*Web Authentication: An API for accessing Public Key Credentials*, especially RP ID scoping and registration/assertion verification](https://www.w3.org/TR/webauthn-3/).

## Web Crypto

Application JavaScript creates, imports, or derives a `CryptoKey` with an algorithm, allowed usages, and an `extractable` flag.
The API requires no human consent ceremony for those operations.

The browser checks the key’s algorithm and usages on each call.
A non-extractable key cannot be exported through the API, but code holding its handle can perform permitted operations.

A `CryptoKey` is not inherently bound to a human, device, or RP ID.
Browser execution and storage boundaries normally isolate access, but applications can explicitly share key objects through supported messaging mechanisms.
Non-extractability does not promise hardware protection.

Web Crypto interprets algorithms and bytes, not app-defined purposes.
Malicious same-origin code can use accessible keys without extracting them.
Thus, non-extractability limits disclosure but does not establish trustworthy use.
See [*Web Cryptography*, “Security considerations,” `CryptoKey`, and `SubtleCrypto`](https://www.w3.org/TR/webcrypto/).

## ssh-agent and gpg-agent

Clients request private-key operations when needed.
The user loads or unlocks keys; agents can then serve repeated requests without repeated passphrase entry.
Keys normally belong to the user’s agent environment, not to individual calling apps or origins.

With `ssh-add -c`, the agent requires confirmation before using the added identity.
`ssh-add -t` limits its lifetime.
Destination constraints restrict permitted SSH destinations and forwarding paths.
The agent checks those constraints when the key is used.
Forwarded enforcement requires cooperating SSH implementations, and the mechanism cannot prevent every onward relay of an exposed agent socket.
See [*ssh-add(1)*](https://man.openbsd.org/ssh-add).

`gpg-agent` mediates signing and decryption and normally caches passphrases.
Its `--ignore-cache-for-signing` option bypasses that cache for signing; a passphrase prompt still does not explain or approve the document’s meaning.
See [*Using the GNU Privacy Guard*, “Agent Options”](https://www.gnupg.org/documentation/manuals/gnupg/Agent-Options.html).

Agents deliberately avoid interpreting application documents.
Access to an unconstrained agent can therefore confer considerable signing authority.
Forwarding exposes that authority to another machine, while poorly explained confirmation prompts can be phished.
See OpenSSH’s [*SSH agent restriction*](https://www.openssh.org/agent-restrict.html).

## Macaroons and object capabilities

A macaroon issuer grants a token to a requester.
Its holder can delegate narrower authority by adding caveats.
There is no built-in human consent schedule or necessary app, device, or user binding.
The receiving service verifies the token and every required caveat on each use.
The token mechanism does not define application semantics; the service must understand its caveats.
Token leakage and incorrect caveat checking remain risks.
See [*Macaroons: Cookies with Contextual Caveats for Decentralized Authorization in the Cloud*](https://research.google/pubs/macaroons-cookies-with-contextual-caveats-for-decentralized-authorization-in-the-cloud/).

An object capability grants authority through an unforgeable reference.
A component receives it from another authorized component, at construction or later delegation.
Human consent is an application choice.
The runtime protects references, while the target object or restricting wrapper enforces permitted calls.
Authority follows possession, rather than an intrinsic user or device identity.
The runtime does not interpret business meaning.
Overbroad references grant excess authority; revocation needs a deliberate design, such as a revocable intermediary.
See [*Capability Myths Demolished*](https://www.erights.org/elib/capability/duals/myths.html).

## What maps onto Small Sea

The Hub fits the keystore or agent role: retain framework secrets and execute authorized operations.
The Manager fits the permission-system role: decide grants under local approval and team authority.
Apps request services and own their data semantics.
This preserves the existing architectural distinction between authorizing a berth and owning an app’s working tree.

**Make session requests the normal consent point.**
An app should request an explicit capability set.
The approval display should identify the requester, team, berth, operations, and duration.
The resulting session should record the granted subset.
Additional capabilities should require another decision, while ordinary calls within the grant should proceed without prompting.

**Separate operation names from purpose labels.**
Use framework-defined operations such as `sign.commit`.
If encryption becomes an exposed service, distinguish operations by their actual authority, such as encrypting for specified recipients versus decrypting berth data.
Do not introduce a blanket `crypto` grant.
Keep algorithms in operation profiles unless choosing an algorithm changes the authority granted.

Represent app purposes separately, for example as an app identifier plus `content`, `registry`, or `merge`.
Use a stable namespace, not a friendly display name.
Apps should define these labels and their explanations in their own protocol documentation or request metadata.
The framework should validate their representation and compare exact values without deciding what “merge” means.

The approved labels belong in session grants and signed delegations.
Signed work contexts must carry the same scope.
Receiving apps decide which purpose they expect for a particular record.
This removes Files-specific lists from framework code without removing purpose checks.

**Create keys when a grant needs them.**
After approval, create or reuse this device’s workhorse key for the berth.
The Manager authorizes the corresponding delegation through the team-device key.
Neither private key reaches the app.
A new session need not create a new key.

**Enforce grants on every call.**
The Hub should check session validity, operation, berth, purpose, and applicable key restrictions.
It should continue checking that the signed context matches those constraints.
That proves scope, not that a purported merge correctly merges anything.

The binding also needs an explicit definition.
A bearer session token identifies possession of an approved session; a claimed app name does not authenticate an executable.

## Where the analogies break

Small Sea signatures outlive the local session.
Teammates’ devices must later verify the signature and delegation against their selected authority views.
None of these local mechanisms supplies that shared authorization model, although WebAuthn has remote verification and Android has remote attestation.

The current `berth_authority.py` judges authority under the verifier’s current view.
The signer’s `authority_view` is evidence, not proof of historical authorization.
“Shared authority” therefore does not imply identical views on every device.

Offline devices cannot immediately learn revocations.
Multiple devices belonging to one person need distinct key identities and explicit authority relationships.
Ending a session, withdrawing a delegation, removing a device, and deciding whether to accept old signatures are separate decisions.

Encryption adds another limit: revoking access cannot retract plaintext or keys already obtained.
When authority is missing or disputed, Small Sea can preserve the work and pause acceptance for human resolution.

## Open questions for the owner

- What identifies the requesting app: an approved connection, an executable identity, or another binding?
- Should approval last for one session, or may the Manager remember a grant?
- Which capabilities need per-use confirmation?
- Who may delegate an app-defined purpose, and what does the approval display promise about its meaning?
- Does revocation reject previously unseen old work, future work, or both?
- Which concrete encryption operations do apps need?
## Owner decisions (2026-09-26)

Prefer something simple and sensible now; defer richer ideas.

- **An app is identified by its name.** The name is not a secret and proves nothing about the executable. Instead, the Manager shows the user every app it knows about, so a lookalike name stands out.
- **No capability scopes inside an app, for now.** An approved session for an app on a team can use every crypto operation the framework offers on that app's berth. Different sessions of one app do not get different permissions.
- **Consent stays where it is: the session PIN approval.** No separate grant step and no per-use confirmation.
- **Purpose labels are the app's business.** The framework checks only that a label belongs to the session's app (for example, that it is namespaced by the app name), not what it means.

Deferred: per-feature or per-client scopes, per-use confirmation, grants remembered beyond sessions, authenticating the executable behind an app name.

Still open: what revocation rejects (unseen old work, future work, or both), and which encryption operations apps need (task 39 inventory).
