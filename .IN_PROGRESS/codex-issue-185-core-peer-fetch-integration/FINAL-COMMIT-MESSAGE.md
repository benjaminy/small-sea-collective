Fetch and park a teammate's Core chain (#185)

`TeamManager.fetch_teammate_core` reads one teammate's Core chain through the
encrypted Hub session and preserves the head it observed under a device-local
ref, without checking anything out or moving local `main`. A head that diverges
from the last one fetched is an observation rather than a failure: the
forward-only convenience ref keeps its head and the new one is recorded under
its own immutable link-scoped ref, so the same link can be fetched again.
`list_core_source_heads` reports those refs alongside the self-publication refs
Cod Sync already parks, deriving supersession and containment from live refs and
Git ancestry so nothing has to be remembered across processes.

Supporting changes: `Repo` gains prefix ref listing and race-safe immutable ref
creation, which Cod Sync's publication settlement now shares; and an
undeliverable peer sender key gets a typed Hub exception and a stable HTTP 409
so it stays distinguishable from an unknown peer route and from an ordinary
provider failure across the Hub and store boundaries.

Fetching remains explicit and unconditional. A parked ref says what bytes this
device read from the store the Hub selected — not who authored them, and not
that they may be integrated.
