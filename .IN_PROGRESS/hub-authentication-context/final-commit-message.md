Bind authenticated publication context at the Hub read boundary (#261)

An encrypted read now establishes that a device the accepted team Core
associates with the teammate this read expected published these exact bytes for
this team, this berth, and this exact logical path.
Previously a valid team payload could be served at any other path, in any other
berth, or as any other teammate's publication, and be accepted.

Cuttlefish group messages authenticate an opaque caller context alongside the
full sender header, under a domain-separated signature transcript and as AEAD
associated data.
The Hub builds that context from the object's logical coordinates, resolves the
signing device's owner in Core's `team_device` projection, and requires it to
match the publisher the operation expected.
Every check runs before plaintext is released, before receiver state is
committed, and before any iteration-driven key derivation.
Missing sender keys, missing or ambiguous ownership, an absent projection, and
refused bytes stay distinguishable to callers, and none of them can reach a
client as success or as an absent object.

Own, peer, and retained-candidate reads get the guarantee.
Passthrough, `/cloud_proxy`, `/bootstrap/cloud_file`, runtime artifact
distribution, and signals remain raw transport; the Hub spec now names each
one's contract, its accepting consumer, and its known gaps instead of
describing encryption as unimplemented.
