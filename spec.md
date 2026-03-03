---
id: small-sea-collective
version: 1
status: experimental
---

## Vision

Small Sea Collective is a framework for implementing support collaborative team applications on top of general-purpose cloud services.
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
There is one special built-in team (NoteToSelf) and one special built-in app (SmallSeaCollectiveCore; "core" for short).
The core app stations is where information about teams is kept: membership, permissions, certificates, etc.
The core app - note to self station is the place where users track their devices, teams, apps, etc.

Concretely, stations are two different things: a way of organizing access to "shared" general-purpose services and a way of organizing data locally.
Locally, Small Sea client apps see multi teams that can use the app.
Clients are free to organize teams however they like, but it's best to keep some primary collection of team data in a separate folder and or git repo.
On the services side, stations are zones, slices, buckets, whatever the appropriate partitioning abstraction is for that service.
For example, if an S3 storage service is a user's primary cloud storage place, each station's data lives in a different bucket.
In the context of notifications, the Hub uses stations to decide what client applications to dispatch notifications to.
In other words, fundamentally stations are about permissions, visibility and routing in the context of "shared" general-purpose services.
Applications are free to use these resources in more or less any way, though the framework supports and encourages specific patterns.

A central piece of Small Sea is the Hub: This is a service that runs locally on any device that a user wants to run a Small Sea app on.
The Hub provides/controls access to the user's general-purpose service providers for the apps.

### Identity

Identity/authentication is one of the key challenges and key innovations in Small Sea.
Common practice today is for digital identities to be anchored to some large centralized organization; for example, alice@google or a CA signing a company's root cert.
The Small Sea Collective project's ambition is to finally make the web of trust work by linking identity to Small Sea teams.
Teammates certify each others' identity, at least is a low-stakes way.
And overlapping team membership at least implicitly defines a web (Alice is in a club with Bob, and Bob works in the same department as Carol).
Users have a whole collection of linked certificates and when team members do things together (ideally in the actual physical world), they can re-up their confidence in each others' identities.
The ambition is for the details of this identity confidence building to be as seamless to/hidden from the users as possible, though the implementation is TBD.

### Teams, Permissions and Invitations

Team management is an interesting challenge, due to the fully decentralized nature of Small Sea.
Teams are not fully owned or controlled by any individual or organization.
The permissions system built in to Small Sea is quite simple.
For each station a user knows about they can have either read-only or read-write permissions.
Since there is no central authority, the meaning of these permissions is a little unconventional.
Read-only means the other team members should arrange their encryption to make that station's data visible to that user.
Read-write means other team members should monitor that user's changes to incorporate into their own.

Individuals can create new teams any time they want.
Users are added to teams by first creating an invitation in the team management app.
This goes in the team's core database like all other team metadata and gets synchronized with the protocol described below.
The invitation is just a cryptographically signed blob of data that can be delivered any which way.
In order to accept an invitation, the new team member must have a Small Sea setup, and supply a link to their cloud location.

A common way of organizing permissions on a team is that some _admin_ members have read-write permission to all the team's stations; normal users have read-write permissions to all the stations except the core (others will not pay attention to any changes they try to make to the team's metadata); _observers_ have read-only permission to all the stations.

### Synchronization

At its base level, Small Sea provides convenient access to general-purpose services.
Above that, some kind of data synchronization is necessary.
Probably in the fullness of time there is room for multiple sync protocols with different strengths and weaknesses.
Out of the box, the Small Sea framework provides one slow, but safe sync mechanism called "corncob".
The idea is to store a station's data in a git repository and then encode changes/deltas as git bundles.
These bundles are uploaded in a cryptographically linked chain to a user's cloud storage location.
Users monitor each other's changes and pull them into their own clones.

## Design Principles

- Small Sea Collective is aligned with the local-first movement, as articulated for example in the essay Local-first software by Kleppmann, Wiggins, van Hardenberg, and McGranaghan.
   To the greatest extent feasible, work should be done on users' own devices, rather than by network services.
- The general-purpose services used to implement application features should know as little about a user's teams and apps as is feasible.
   For example, in the context of storage, all data is end-to-end encrypted with locally managed keys, and only opaque IDs are used for organizing data into folders or buckets.
- All Small Sea communications outside of a single device go through the Hub.
   Applications, sync protocols, and other packages must never make direct network calls.
   The Hub is the sole gateway for all traffic that leaves the device.

## Components

The base Small Sea framework has the following components:

- Hub.
   This is a local service that runs on devices and translates back and forth between Small Sea app requests and the general-purpose services that implement those requests.
   It reads the SmallSeaCollectiveCore/NoteToSelf database to know what services are available, what teams a user belongs to, etc.
- Small Sea encryption layer (name TBD).
   In normal production environments, the Hub encrypts and obscures all communication with services so that very little can be inferred by the general-purpose services providers about what users are doing with them.
   This should be mostly transparent to Small Sea apps.
- Corncob.
   This is safe and slow sync library that is based on git and encodes deltas as a chain of git bundles.
- harmonic-merge.
   This is a library to support merging concurrent changes to an application's state, and conflict resolution when safe automatic merging is not possible.
- Small Sea Client.
   This is a utility library for applications communicating with the Hub.
   It helps manage sessions, makes common workflows easy, etc.
- Team Manager.
   This is the essential built-in user application.
   It manages not only team membership, but devices, general-purpose service subscriptions, etc.
- Shared File Vault.
   This is an example user application.
   It is a Small Sea based file sharing app.

## Typical Application Flow

In normal use, people work locally with some client for a Small Sea app (Small Sea is inspired in part by the local-first movement, after all).
(The client can be a GUI, cli, AI agent, whatever.)
When synchornization happens can either be fully driven by users, or some background automation can help.
When a user wants to share their changes with their team, the following flow happens:
1. The client starts a session with the locally running Hub (if a session has not already been established)
   - Clients are free to speak the Hub's http API directly, but using the Small Sea client lib is available for convenience.
2. The Hub sends a message to the user through the OS notification system to help ensure that client software only accesses stations for apps that the user wants that client to access.
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
App developers are free to make their own synchronization frameworks, but Corncob is a convenient place to start:
Put all the app's data in appropriate station folders, make each one a git repo and use corncob to sync.
Probably the most work app developers have to do specifically related to Small Sea is managing concurrent change conflicts.
harmonic-merge provides some help with conflict resolution, but apps have to handle some of that.

## Open Questions

- In the normal deployment case, a single instance of the Small Sea Hub will run on a device to manage access to services for all a single users' teams and apps.
   It is undecided whether a single hub instance could simultaneously serve two users working on the same device.
   This seems like unnecessary complexity, but there may be a compelling use case for it.
