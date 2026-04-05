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

### Why the boundary is workable

- The identity/trust modules are already a self-contained cluster:
  `keys`, `identity`, `ceremony`, `trust`.
- The session-crypto modules are already a self-contained cluster:
  `prekeys`, `x3dh`, `ratchet`, `group`.
- `prekeys.py` stays in `cuttlefish` for now because it is part of X3DH
  session bootstrap, even though its `IdentityKeyPair` name overlaps with
  the broader trust-side notion of identity.

## Approach

1. Create `packages/wrasse-trust/` with:
   - `pyproject.toml` (same shape as cuttlefish: hatchling, cryptography dep;
     distribution name `wrasse-trust`, importable package `wrasse_trust`)
   - `wrasse_trust/` package directory with `__init__.py` (initially empty,
     same as cuttlefish's)
   - `tests/` directory
2. Move the four identity/trust modules into `wrasse_trust/`, updating
   intra-package imports.
3. Move `test_identity.py` to `wrasse-trust/tests/`, updating its imports
   from `cuttlefish.*` to `wrasse_trust.*`. Check first that the test file
   doesn't also cover anything staying in cuttlefish; split if needed.
4. Update docs to match the new boundary:
   - `packages/cuttlefish/README.md` — most of section 2 (Key-Based Identity,
     key hierarchy, WoT, ceremonies, revocation) plus related prior art and
     open questions move to wrasse-trust. This is roughly 60–70% of the file.
   - `packages/wrasse-trust/README.md` — new home for the moved sections.
   - `architecture.md` — line 55 currently describes cuttlefish as owning both
     encryption and web-of-trust identity; update to reflect the split.
5. Add one short clarifying note in `cuttlefish/prekeys.py` about its
   X3DH bootstrap identity keys vs the broader trust-side identity model.
6. Make this a clean break:
   - do not leave compatibility imports in `cuttlefish`
   - do not add transitional re-exports or alias packages
   - update all in-repo imports to the new package names directly
7. Tighten any awkward boundaries revealed by the move, but do not redesign
   identity/encryption binding in this branch.

## Validation

- `uv run pytest packages/cuttlefish/tests/test_ratchet.py`
- `uv run pytest packages/cuttlefish/tests/test_x3dh.py`
- `uv run pytest packages/cuttlefish/tests/test_group.py`
- `uv run pytest packages/wrasse-trust/tests/test_identity.py`
- `rg "from cuttlefish\\.(keys|identity|ceremony|trust)|import cuttlefish\\.(keys|identity|ceremony|trust)" packages`
  finds no remaining imports
- the moved modules no longer exist under `packages/cuttlefish/cuttlefish/`
- `README.md`, `architecture.md`, and the two package READMEs no longer
  describe `cuttlefish` as owning both encryption and trust

## Risks To Watch

- `prekeys.py` may still feel ambiguously named after the split
- docs may still describe `cuttlefish` as containing both concerns
- the hard break means any new in-repo imports must be updated in the same
  branch rather than carried through compatibility shims
