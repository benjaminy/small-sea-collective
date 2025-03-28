# Small Sea

### Teams and Utility-Service-Only Sync for Local-First Software

Around 2020 the Local-First community started to coallesce around discontent with cloud-oriented software architectures.
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

`uvicorn small_sea_local_hub:app --reload --port 11437`

## The Name 'Small Sea'

It's kind of a pun

`rclone serve webdav --addr :PORT LOCAL_PATH --vfs-cache-mode full`