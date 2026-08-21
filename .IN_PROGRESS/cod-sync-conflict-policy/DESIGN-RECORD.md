# Cod Sync publication settlement

Cod Sync settles one immutable attempted Git state within a fixed envelope of one head write and at most two validated observation passes.
It does not retry internally or construct a successor after contention.
A later invocation begins from a new observation only after the previous invocation is known to be closed.

Stored Git history and write closure answer separate questions.
Validated containment can establish `already_present`, while divergence requires application integration; neither is allowed to settle an attempted write that may still take effect.
This separation is why `outcome_unresolved` outranks the observed Git relationship when a store contradicts its own settlement contract.

Divergent observed heads are preserved mechanically under an immutable Cod Sync-owned parked ref.
Cod Sync never chooses the semantic merge.
Applications own integration because Core, NoteToSelf, and `ssc-files` have different repository and checkout contracts.

An etag on a non-empty chain is settlement evidence as well as a compare-and-swap input.
Cod Sync refuses to extend a chain without a comparable etag, while storage-boundary enforcement remains follow-up #199.
The local store makes only the chain head atomically visible; partial write-once bundles and archived links remain unreachable until a complete head names them.
