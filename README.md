# <img src="./Documentation/Images/wrasse-med.png"> Small Sea Collective

### Local-_First_, General-Purpose Services _Second_

Around 2020 the Local-First community coallesced around discontent with cloud-oriented software architectures and SaaS business models.
[Local-first software](https://www.inkandswitch.com/essay/local-first/) from Ink & Switch is widely seen as the community's founding manifesto.
(If you're unfamiliar with local-first, this page will make more sense if you skim the Ink & Switch essay first.)

Many have commented on a pragmatist-idealist spectrum in local-first.
On the pragmatist end, the back-end architectures and business models are similar to conventional cloud-based applications.
The _local-first_ part is focused on smart caching and synchronization to support offline mode and less waiting on responses from services (No Spinners!).
This is better than a poke in the eye with a sharp stick, but it fails to address some of the more ambitious local-first ideals.

The Small Sea project is firmly on the idealist side.
We want to see more applications deployed with little (or even zero) dependence on bespoke/application-specific services.
Applications that people can use without fear of their data being used against their interests; with confidence that their ongoing use of the application doesn't depend on some business keeping the lights on.
But of course network services are essential for many features that we expect from software.
So the challenge this project takes on is decoupling applications on one side from _general-purpose_ services on the other.
By general-purpose services, we mean things like:

- Internet service providers
- Storage (Dropbox, S3, etc)
- Notifications (ntfy, SuprSend, etc)
- Peer-to-peer streaming connections (Tailscale, ZeroTier, etc)
- Identity verification (certificate authorities)

To put it in visual terms, conventional SaaS application architectures look something like:

<table>
<tr>
<th>Features ⮕</th>
<th><img src="./Documentation/Images/cloud-storage.png" alt="Cloud storage" title="Cloud storage"></th>
<th><img src="./Documentation/Images/meeple-team.png" alt="Team management" title="Team management"></th>
<th><img src="./Documentation/Images/notifications.png" alt="Notifications" title="Notifications"></th>
<th><img src="./Documentation/Images/sync-engine.png" alt="Synchronization" title="Synchronization"></th>
<th><img src="./Documentation/Images/streaming-media.png" alt="Streaming media" title="Streaming media"></th>
<th><img src="./Documentation/Images/security-etc.png" alt="Security, privacy, auth" title="Security, privacy, auth"></th>
</tr>
<tr style="vertical-align:top">
<th>Apps ⬇</th>
<td>Storage</td>
<td>Team<br/>Management</td>
<td>Notifications</td>
<td>Sync</td>
<td>Streaming</td>
<td>Security,<br/>Privacy</td>
</tr>
<tr>
<td><img src="./Documentation/Images/slack-icon.png" alt="Slack logo" title="Slack"/></td>
<td><div>
  <img src="./Documentation/Images/slack-icon.png">
  <img src="./Documentation/Images/cloud-storage.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/slack-icon.png">
  <img src="./Documentation/Images/meeple-team.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/slack-icon.png">
  <img src="./Documentation/Images/notifications.png"
       style="position: relative; top: 5px; left: -20px; width: 25px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/slack-icon.png">
  <img src="./Documentation/Images/sync-engine.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/slack-icon.png">
  <img src="./Documentation/Images/streaming-media.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/slack-icon.png">
  <img src="./Documentation/Images/security-etc.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
</tr>
<tr>
<td><img src="./Documentation/Images/discord-logo.png" alt="Discord logo" title="Discord"></td>
<td><div>
  <img src="./Documentation/Images/discord-logo.png">
  <img src="./Documentation/Images/cloud-storage.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/discord-logo.png">
  <img src="./Documentation/Images/meeple-team.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/discord-logo.png">
  <img src="./Documentation/Images/notifications.png"
       style="position: relative; top: 5px; left: -20px; width: 25px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/discord-logo.png">
  <img src="./Documentation/Images/sync-engine.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/discord-logo.png">
  <img src="./Documentation/Images/streaming-media.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/discord-logo.png">
  <img src="./Documentation/Images/security-etc.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
</tr>
<tr>
<td><img src="./Documentation/Images/ms-teams-logo.png" alt="MS Teams logo" title="MS Teams"></td>
<td><div>
  <img src="./Documentation/Images/ms-teams-logo.png">
  <img src="./Documentation/Images/cloud-storage.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/ms-teams-logo.png">
  <img src="./Documentation/Images/meeple-team.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/ms-teams-logo.png">
  <img src="./Documentation/Images/notifications.png"
       style="position: relative; top: 5px; left: -20px; width: 25px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/ms-teams-logo.png">
  <img src="./Documentation/Images/sync-engine.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/ms-teams-logo.png">
  <img src="./Documentation/Images/streaming-media.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
<td><div>
  <img src="./Documentation/Images/ms-teams-logo.png">
  <img src="./Documentation/Images/security-etc.png"
       style="position: relative; top: 5px; left: -20px; width: 30px; height: auto;">
</div></td>
</tr>
</table>

Each application implements its own version of more or less standard services.
The application and the services are bundled as a single thing.

In the Small Sea framework, the Small Sea Hub provides a collection of generic services to applications and implements those with whatever general-purpose services the user subscribes to.

<img src="./Documentation/Images/small-sea-hub.png" alt="Small Sea Hub" title="Small Sea Hub">

The Small Sea Hub is **not** a service on the internet.
Rather it's software that runs on client devices and translates local application requests into approriate general-purpose service requests.
In some cases this translation is a fairly simple pass-through; in some cases the Hub provides quite a lot of protocol smarts on top of the generic service.

## Two Technical Pillars: Team Management and Snapshot-Based 3-Way Merge

One of the known hard problems in local-first is identity/team management.
We want to use apps to share things with other people over the internet.
But unless that sharing is globally public, it gets hard to control who gets to read and write what data.
This is an extremely important role played by big services like MS Teams, Google Workspace, etc.

The Small Sea project includes fully decentralized team management.
Team members publish their modifications of the team's data to their own cloud storage location.
Signal-inspired [cryptographic](https://signal.org/docs/specifications/x3dh/) [protocols](https://signal.org/docs/specifications/doubleratchet/) ensure only team members can read the team's data.
New members are added with a multi-step invitation process.
When members leave, key rotation ensures that they can't read future modifications.
Periodic out-of-band certificate validation helps ensure imposters aren't impersonating team members.

And a central goal is to wrap all this crypto and protocol fanciness up with an approachable UI/UX, because we know Johnny Can't Encrypt.

Synchronizing/merging/reconciling concurrent modifications to some document/database is arguably the central technical challenge in local-first software.
For example, it is one of the most common topics on the [Local-First Podcast](https://www.localfirst.fm/).
The dominant framework for sync seems to be CRDTs.
CRDTs are cool, but relying on them makes me nervous.
It feels like it's too easy to make a mistake with them that could lead to data corruption in corner cases.
And requires too much from-scratch thinking on the part of application programmers.

So the baseline sync method in Small Sea is another old friend: snapshot-based 3-way merge.
In other words, `git`.
This approach to sync is slow.
It's not appropriate for real-time concurrent editing of some doc where users want to see each others' edits with minimal latency.
But for applications where occasional, slow sync is sufficient it has some compelling advantages:

- Full-environment snapshots are the basic unit, so it's relatively easy to avoid mixing edits together in a way that ends up breaking some invariant
- When finding a good merge/reconcilation gets hard it is easy to fall back to _pick yours_ or _pick mine_
- It's relatively easy to integrate with existing software, starting with coarse yours-or-mine reconciliation and incremental improvement to automatic merge logic

One way to look at this is the classic N×M problem.

So to refine the motivating question: How much application functionality can be implemented by stitching together general-purpose services like these, instead of the application creator providing them directly?

### Why?

There has been a little explosion of projects in this space, and a couple of challenges have emerged as especially tricky:

- Identity, especially as it relates to sharing.
  In cloud architectures, it's natural for people to register accounts with central services.
  It's then natural for these accounts to be used as digital identities.
  Especially popular services have become de facto identity providers for a great many applications.
  Sharing between different identities within a particular ecosystem can be managed in a straightforward way by the service provider.
  In local-first architectures it's not obvious how to handle digital identity and manage sharing.
- Synchronization was recognized very early as a big challenge.
  Research on CRDTs was some of the first work in the local-first space.
  But decentralized data synchronization in its full generality is a very hard problem that is unlikely to have a one-size-fits-all solution any time soon.

The Small Sea projects leans in hard to using git as its synchronization framework.

General Purpose Services

<img src="./Documentation/Images/wrasse-small.png">
<img src="./Documentation/Images/wrasse-small2.png">

## Obvious Caveat: General-Purpose Services Can't Do Everything

For many applications/features there's no obvious way to implement them with only general-purpose services:

- Anything with specific real-time data feeds (traffic, weather, stock prices, etc)
- E-commerce
- Broadcast-to-the-universe style social media

Small Sea is not an attempt to re-implement these things in a different style.
It's for apps where small groups of people share and collaborate amongst each other.

## If Not SaaS How will Devs Feed their Families?

SaaS business models are overwhelmingly dominant in software development today, but it has not always been thus.
I am not opposed to developers getting paid for their efforts.
But I don't think SaaS models are the only way.
If Small Sea is successful, maybe new models will emerge.
And a nice feature of the model is that it requires no operating costs on the part of application authors.
So even if the model is only marginally successful, it might open the door to some applications that aren't getting made today.

## What about my Sync Engine?

In the Local-First community, sync engines are a common topic of conversation.
Details vary somewhat, but what I mean is any service that facilitates synchronization between nodes that are concurrently modifying some document/database.
I'm not sure it's feasible to make a sync server that does not see the contents of the data it's syncing.
And that's something I'm really trying to avoid.
Maybe in the future a really hands-off general purpose sync engine protocol will emerge.
But for the time being, I don't think such a thing exists.

## The Name 'Small Sea'

It's kind of a pun
