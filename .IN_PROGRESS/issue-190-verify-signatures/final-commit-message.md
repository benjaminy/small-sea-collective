# Draft final commit message

Sign Cod Sync commits and verify stored history at acceptance

Add optional SSH signing across commit creation paths and an explicit-key verifier for fetch and publication observation.
Check original, complete history before accepting it, including objects left by rejected attempts; reject invalid signing configuration rather than creating unsigned commits.
Micro tests cover signing, history overrides, incremental rejection and retry, and verification setup failures.

Runtime verification remains deferred until berth-scoped keys and Manager wiring supply the policy inputs.
Existing link signatures remain unchanged.
