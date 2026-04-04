# Branch Plan

## Goal

Follow up on GitHub issue `#38` by splitting the current `cuttlefish` package
into two narrower packages:

- `cuttlefish` stays the encryption and session-crypto package
- `wrasse-trust` becomes the identity, certification, and trust package

The goal is clearer boundaries, easier future evolution, and a package layout
that is easier for both humans and AI coding assistants to navigate.

### Why "wrasse-trust"?

The wrasse is the mascot for the whole Small Sea project, so plain `wrasse`
would be ambiguous. The `-trust` suffix distinguishes this as the package
that owns identity and trust-chain logic specifically.

## Proposed Split

Keep in `cuttlefish` (session crypto):

- `prekeys.py` — X3DH prekey bundles and identity key pairs for session init
- `x3dh.py` — Extended Triple Diffie-Hellman key agreement
- `ratchet.py` — Double Ratchet for 1:1 sessions
- `group.py` — Sender Keys for group messaging

Move to `wrasse-trust` (identity and trust):

- `keys.py` — BURIED/GUARDED/DAILY key hierarchy and key collections
- `identity.py` — certificates and CA-style key hierarchy
- `ceremony.py` — key signing ceremony helpers
- `trust.py` — trust chain traversal and cert graph

### Why the boundary is clean

The two clusters have **zero cross-imports** today:
- The identity cluster (`keys` → `identity` → `ceremony`, `trust`) is
  self-contained.
- The session-crypto cluster (`prekeys` → `x3dh`, `ratchet`, `group`) is
  self-contained.
- `prekeys.py` defines X25519/Ed25519 key pairs for X3DH session bootstrap —
  these are conceptually different from the BURIED/GUARDED/DAILY identity
  hierarchy in `keys.py`, despite both being "identity keys."

## Approach

1. Create `packages/wrasse-trust/` with:
   - `pyproject.toml` (same shape as cuttlefish: hatchling, cryptography dep)
   - `wrasse_trust/` package directory with `__init__.py` (exposing core types)
   - `tests/` directory
2. Move the four identity/trust modules into `wrasse_trust/`, updating
   intra-package imports.
3. Move `test_identity.py` to `wrasse-trust/tests/`, updating its imports
   from `cuttlefish.*` to `wrasse_trust.*`.
4. Update `packages/cuttlefish/cuttlefish/__init__.py` to export core session crypto types.
5. Split `packages/cuttlefish/README.md`:
   - Keep encryption/ratchet/group sections in `cuttlefish`.
   - Move identity/trust/ceremony/wot sections to `wrasse-trust/README.md`.
   - Update "Module Map" and "Status" in both.
6. Add a clarifying comment in `cuttlefish/prekeys.py` regarding its `IdentityKeyPair` vs `wrasse-trust` identity.
7. Verify no edits needed to the cuttlefish `pyproject.toml` — hatchling
   auto-discovers modules, so removing files just works.
8. Tighten any awkward boundaries revealed by the move.

## Identity ↔ Encryption Binding (Future Work)

Today the identity system (wrasse-trust) and the session-crypto system
(cuttlefish) are fully independent — there is no code that cryptographically
links "this encrypted message was sent by this identity." In the long run this
binding is essential: teammates need to verify that data pushed, sent, or
posted is authentically tied to a specific identity in the trust graph.

This branch is **not** the place to design that binding, but we should be
aware that the clean split also means neither package currently depends on
the other. When the binding is built, it will likely live in a third
integration point (or one package will gain an optional dependency on the
other). The split should not make that future integration harder — and
since both clusters are already independent today, it doesn't.

## Validation

- `cuttlefish` tests still pass after the split (`test_ratchet`, `test_x3dh`,
  `test_group`)
- new `wrasse-trust` tests pass (`test_identity` moved and updated)
- no Small Sea package is left importing moved modules from the old location
- README / architecture language still matches the actual package boundaries

## Risks To Watch

- docs may still describe `cuttlefish` as containing both concerns
- if any package starts importing from cuttlefish before this lands, those
  imports will need updating (currently no external consumers exist)
