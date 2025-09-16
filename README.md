# Small Sea

### General Purpose Services for Local-First Software

Around 2020 the Local-First community started to coallesce around discontent with cloud-oriented software architectures.
(The ["Local-first software" manifesto](https://www.inkandswitch.com/essay/local-first/) from Ink & Switch in widely seen as an important milestone.)

Many have commented on a pragmatist-idealist spectrum in the Local-First community.
On the pragmatist end, the back-end architectures and business models are similar to conventional cloud-based applications.
The Local-First part is focused on smart caching and synchronization to support offline editing and less waiting on responses from services (No Spinners!).

The Small Sea project is firmly on the idealist side.
The motivating question is: How far can we get with no application-specific services at all?
In other words, deploy an application with no backend in the usual sense at all.

Of course not having network services at all is severely limiting, so this brings us to: General Purpose Services.
That is, services that not not connected to any specific application.
Such as:

- Internet service providers
- Storage (Dropbox, S3, etc)
- Notifications (ntfy, SuprSend, etc)
- Peer-to-peer connections (Tailscale, ZeroTier, etc)
- Identity verification (certificate authorities)

So to refine the motivating question: How much application functionality can be implemented by stitching together general-purpose services like these, instead of the application creator providing them directly?

Aside on natural services.
Some applications align naturally with application-specific services.
Live traffic and weather data.
E-commerce.
Broadcast-to-the-universe style social media.
Small Sea is not an attempt to re-implement these things in a different style.
It's for apps where small groups of people are sharing and collaborating amongst each other.

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


`uvicorn --app-dir Source small_sea_local_hub:app --reload --port 11437`

## The Name 'Small Sea'

It's kind of a pun


`rclone serve webdav --addr :PORT LOCAL_PATH --user USER --pass SECRET --etag-hash --vfs-cache-mode full`

rclone serve webdav --addr :2345 /tmp/qwe --user alice --pass abc123 --vfs-cache-mode full

curl -u USER:SECRET -X PROPFIND LOCAL

curl -u USER:SECRET -O url

curl -u USER:SECRET  -T file url

curl -u USER:SECRET -X DELETE url