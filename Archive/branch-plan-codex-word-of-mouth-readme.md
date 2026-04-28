# Branch Plan: Word of Mouth Concept README

**Branch:** `codex-word-of-mouth-readme`
**Base:** `main`
**Kind:** Product/design documentation plus package workspace stub.
**Status:** Wrapped.

## Purpose

This side quest turns the tiny `packages/word-of-mouth/README.md` fragment into a serious concept document for the future Word of Mouth app, without implementing application behavior.

The app idea is intentionally adjacent to broadcast social media, but it must still fit Small Sea's core commitments:

- Small Sea apps use the Hub as their internet gateway.
- Apps do not read or write Manager-owned team databases directly.
- Teams are human-scale, socially meaningful groups rather than global audience buckets.
- Ambiguous or conflicting social state should be visible instead of silently resolved.

The branch also adds the smallest package metadata stub needed because `packages/*` is included in the `uv` workspace and a README-only package currently breaks test collection.

## Branch Goals

1. Expand the README into a concept document that captures the product thesis, audience, core loop, propagation model, threat model questions, and non-goals.
2. Include sharp comparison points so future design does not accidentally reinvent Nostr, Secure Scuttlebutt, ActivityPub, AT Protocol, Briar, or Farcaster without noticing.
3. Ask challenging questions directly in the README, especially around consent, provenance leakage, moderation, deletion, spam, incentives, and whether "teams" remain the correct routing primitive.
4. Keep implementation deliberately at stub level: package metadata and an importable empty module only.
5. Validate that workspace-level test collection no longer fails because of the new package directory.

## Skeptic-Grade Validation Plan

This is a design-doc branch, so the validation needs to prove both that the repo is mechanically intact and that the design work improved the decision surface.

Mechanical validation:

- `uv run pytest --collect-only -q` should no longer fail on `packages/word-of-mouth`.
- `git diff --check` should be clean.
- The package should be an ordinary workspace member with `pyproject.toml` and an importable `word_of_mouth` module.

Design validation:

- README must clearly distinguish Word of Mouth from global broadcast social media.
- README must name the strongest comparison projects and explain what to borrow, reject, or study from each.
- README must preserve architectural constraints: Hub gateway, Manager database exclusivity, local-only testing, and human-scale teams.
- README must contain explicit open questions that would change the first real implementation slice.
- README must identify plausible first micro tests without pretending the app exists yet.

## Non-Goals

- No feed UI.
- No protocol implementation.
- No database schema.
- No Hub API changes.
- No migration or backward compatibility work; this is pre-alpha concept shaping.

## Final Validation

- `uv run pytest --collect-only -q` -> 350 tests collected.
- `git diff --check` -> clean.
- `uv run python -c "import word_of_mouth; print(word_of_mouth.__version__)"` -> `0.1.0`.

## Outcome

The README now frames Word of Mouth as a team-mediated social relay app rather than a public feed clone. It names the closest prior art, especially Secure Scuttlebutt and Briar, and contrasts them with Nostr, ActivityPub, AT Protocol, and Farcaster. It records the hard open questions around consent, relay visibility, deletion, moderation, social laundering, spam, team routing pressure, and the first niche.

The package has a minimal `pyproject.toml` and importable `word_of_mouth` stub so the new package directory can live in the workspace without breaking collection.

## Follow-Up Concept Pass

After review, the README was tightened around the sacred invariant: propagation is driven by team membership overlap, not friends-of-friends. The doc now names the `membership-overlap bridge` as the central primitive, softens "outward-facing" into "beyond-one-team," and adds `relayable_artifact` as an explicit export boundary so Word of Mouth does not become a generic permission bypass for arbitrary Small Sea content.
