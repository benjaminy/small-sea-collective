---
id: small-sea-collective
version: 1
status: experimental
---

## Vision

Small Sea Collective is a framework for building collaborative team applications on top of general-purpose cloud services.
As a familiar starting point you can think about apps like Slack and Microsoft Teams.
However, the immediate goal is not to re-implement or replace establish apps.
Rather, the goal is to open a new frontier for apps for which there's not a strong enough incentive to cover the cost of operating back-end services for the app.
Also, since no application service provider has access to teams' data by default, strong privacy preservation is more feasible.
Many features that are usually implemented with application-specific back-end services can be implemented in the Small Sea Collective framework with user-rented general-purpose services under the hood.
General-purpose cloud services include things like:
- Storage (e.g. Dropbox)
- Messaging/eventing/notifications (e.g. ntfy)
- Real-time connectivity (e.g. Tailscale)
- Authentication (e.g. Auth0)

Small Sea Collective is entirely decentralized.
There is no global Small Sea service or registry or anything.
And it encourages applications to be developed along similar lines.
The emphasis is more on helping small, well-connected teams of people work and play better, rather than facilitating discovery between loosely connected people.

The Small Sea framework cannot prevent application developers from using their own centralized services.
The framework minimizes the need for such services.
But no attempt is made to actually block or impede application developers from writing whatever software they want.

## Core Concepts

The core abstractions in Small Sea are teams and applications.
The intersection of a specific team and a specific app is called a _station_.
From an application's perspective each team "lives" in a different partition/station.
From a team's perspective, each app "lives" in a different partition/station.
There is one special built-in team (NoteToSelf) and one special built-in app (SmallSeaCollectiveCore; "core" for short).
Primary team data like membership, permissions, certificates, for each team is kept in the relevant _team_-core station.
The NoteToSelf-core station is where Small Sea user information is kept, like devices, teams, apps, etc.

Fundamentally stations are ways of organizing access to "shared" general-purpose services.
Some examples:
- In the context of an S3 storage service, each station corresponds to a different bucket.
- Notifications are sent to a particular station, and on the receiving side are routed to listeners based on stations.
- Live VPN connections for streaming data are made based on stations.
In order for an application to access any of these resources, the Hub needs to know which station (which for a particular app, means which team) it wants to access.
The application has to start a session to gain such access, which gives users an opportunity to decide if they want that app client to access those resources.

On the local side, apps are free to organize their data however they like, but will have an easier time integrating with Small Sea if teams are partitioned clearly.
For example, team-specific data should be kept in separate folders that can be git repos to integrate with CornCob.

A central piece of Small Sea is the Hub: This is a service that runs locally on any device that a user wants to run a Small Sea app on.
The Hub provides/controls access to the user's general-purpose service providers for the apps.

### Identity

Identity/authentication is one of the key challenges and key innovations in Small Sea.
Common practice today is for digital identities to be anchored to some large organization; for example, alice@google or a CA signing a company's root cert.
The Small Sea Collective project's ambition is to finally make the web of trust work by linking identity to Small Sea teams.
Teammates certify each others' identity, at least is a low-stakes way.
And overlapping team membership at least implicitly defines a web (Alice is in a club with Bob, and Bob works in the same department as Carol).
Users have a whole collection of linked certificates and when team members do things together (ideally in the actual physical world), they can re-up their confidence in each others' identities.
The ambition is for the details of this identity confidence building to be as seamless to/hidden from the users as possible, though the implementation is TBD.

### Teams, Permissions and Invitations

Team management is an interesting challenge, due to the fully decentralized nature of Small Sea.
Teams are not owned or controlled by any individual or organization.
Team data is kept in sync by voluntary pulling and merging by each team member.
The permissions system built in to Small Sea is quite simple.
For each station, a user can have either read-only or read-write permissions.
The meaning of these permissions is more of a social contract than strong technical enforcement.
Read-only means the other team members should arrange their encryption to make that station's data visible to that user.
Read-write means other team members should monitor that user's changes to incorporate into their own.
The Hub is written to follow these rules, but in principle it would be easy to make a Hub workalike that does not follow them.
Each user runs their own instance of the Hub.

Individuals can create new teams any time they want.
The lack of a central service makes the protocol for adding team members a bit complex.
Any user with write permission to a team's Core station can invite others.

Users are added to teams by a moderately complex invitation and acceptance protocol.
The details of this protocol are in the Team Manager package.
It can be made more convenient if the invitee is already a Small Sea user with pre-configured keys in a manner similar to the chat initialization protocol in Signal.

A common way of organizing permissions on a team is that some _admin_ members have read-write permission to all the team's stations; normal users have read-write permissions to all the stations except the core (others will not pay attention to any changes they try to make to the team's metadata); _observers_ have read-only permission to all the stations.

Removing users from a team is even weirder compared to conventional centralized models.
Any admin user can remove another user from the team database and push that change.
In order for this change to actually be effective, all the teammates need to do a key rotation and not share the new keys with the departing member.
If teammates disagree about the membership, it's relatively easy to end up with a weirdly forked team (fork in the sense of blockchains or version control databases).

### Synchronization

At its base level, Small Sea provides convenient access to general-purpose services.
Above that, some kind of data synchronization is necessary.
Probably in the fullness of time there is room for multiple sync protocols with different strengths and weaknesses.
Out of the box, the Small Sea framework provides one slow, but safe sync mechanism called "CornCob".
The idea is to store a station's data in a git repository and then encode changes/deltas as git bundles.
These bundles are uploaded in a cryptographically linked chain to a user's cloud storage location.
Users monitor each other's changes and pull them into their own clones.

In order to fully participate in CornCob sync, each member needs to have their own cloud storage location where their chain of bundles is uploaded.
This location is part of the member's data in the team database.
This architecture is clearly space inefficient in the sense that each member stores a full copy of the team's data.
For many kinds of data and applications, storage has become cheap enough that this price is well worth paying.
For applications with large data (e.g. video editing), it will probably be necessary to refine or supplement the protocol to have less duplication.

## Design Principles

- Small Sea Collective is aligned with the local-first movement, as articulated for example in the essay Local-first software by Kleppmann, Wiggins, van Hardenberg, and McGranaghan.
   To the greatest extent feasible, work should be done on users' own devices, rather than by network services.
- The general-purpose services used to implement application features should know as little about a user's teams and apps as is feasible.
   For example, in the context of storage, all data is end-to-end encrypted with locally managed keys, and only opaque IDs are used for organizing data into folders or buckets.
- All Small Sea communications outside of a single device go through the Hub.
   Applications, sync protocols, and other packages must never make direct network calls.
   The Hub is the sole gateway for all traffic that leaves the device.
   - This is not meant to forbid apps from talking to the internet to do whatever other stuff thy want to do.
      It's just a restriction on Small Sea communications; apps shouldn't try to reimplement or augment the protocol on their own.

## Components

The Small Sea framework has the following components:

- Hub.
   This is a local service that runs on devices and mediates access to all a user's general-purpose services.
   For example:
      - Apps upload and download files through the hub, which puts them in the correct folder or bucket.
      - Apps can send and receive notifications which the Hub routes to the correct apps/teams on the other side.
      - Apps can create create live VPN connections which the Hubs running on the various devices negotiate access to.
   The Hub has a special relationship with the built-in Team Manager app, which manages teams, invitations, apps, service accounts, etc.
- Small Sea encryption layer (name TBD).
   In normal production environments, the Hub encrypts and obscures all communication with services so that very little can be inferred by the general-purpose services providers about what users are doing with them.
   This should be mostly transparent to Small Sea apps.
- CornCob.
   This is safe and slow sync library that is based on git and encodes deltas as a chain of git bundles.
- harmonic-merge.
   This is a library to support merging concurrent changes to an application's state, and conflict resolution when safe automatic merging is not possible.
- Small Sea Client.
   This is a utility library for applications communicating with the Hub.
   It helps manage sessions, makes common workflows easy, etc.
- Team Manager.
   This is the essential built-in user application.
   It manages not only team membership, but devices, general-purpose service subscriptions, etc.
   This info is all stored in a database that the Hub also needs to do its work.
- Shared File Vault.
   This is an example user application.
   It is a Small Sea based file sharing app.
- Permanent Record.
   This is another example application: Chat, with an emphasis on never losing chat logs.

## Typical Application Flow

In normal use, people work locally with some client for a Small Sea app (Small Sea is inspired in part by the local-first movement, after all).
(The client can be a GUI, cli, AI agent, whatever.)
The timing of synchronization can be on-demand by users, triggered by some background automation, or whatever.
When a user wants to share their changes with their team, the following flow happens:
1. The client starts a session with the locally running Hub (if a session has not already been established)
   - Clients are free to speak the Hub's http API directly, but the Small Sea client lib is available for convenience.
2. The Hub sends a message to the user through the OS notification system to help ensure that client software only accesses stations that the user wants that client to access.
3. The client commits its changes to its local git repository and makes a bundle for the most recent changes.
   - This bundle is linked by hash chaining to the previous bundle.
4. The client uploads this new bundle via the Hub to the user's cloud storage (and the Hub knows where it goes, because this is all within the context of a session).
   - It is important to use some kind of concurrency control like etag if-match to ensure that the head of bundle chain file was not concurrently updated by a different client working on the user's behalf.
5. If the team has some notification service, a notification can be sent to alert teammates of the new data.
6. Teammates either receive notifications or poll to get updates.
7. Their clients download the bundles via their local Hub and use git to merge it into their local clone.
8. Conflicts will naturally occur sometimes, and are inevitably at least somewhat application-specific. But Small Sea provides a library called harmonic-merge to help with conflict resolution.

## An App Developer's Perspective, Briefly

App developers should do all team interaction through the Hub.
Much of the API is yet to be developed, but eventually there will be local notifications for things like addition of teammates, availability of new data, etc.
App developers are free to make their own synchronization frameworks, but CornCob is a convenient place to start:
Put all the app's data that needs to be synchronized in appropriate team folders (with NoteToSelf for general app stuff that isn't shared with any 'real' team).
Make each folder a git repo and use CornCob to sync.
Probably the most work app developers have to do specifically related to Small Sea is managing concurrent change conflicts.
harmonic-merge provides some help with conflict resolution, but apps have to handle some of that.

## Open Questions

- In the normal deployment case, a single instance of the Small Sea Hub will run on a device to manage access to services for all a single users' teams and apps.
   It is undecided whether a single hub instance could simultaneously serve two users working on the same device.
   This seems like unnecessary complexity, but there may be a compelling use case for it.
