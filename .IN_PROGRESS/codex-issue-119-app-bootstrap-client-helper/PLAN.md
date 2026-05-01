# Issue 119: App Bootstrap Client Helper

## Goal

Add a `small-sea-client` helper for Hub `409 app_bootstrap_required` responses.
Apps should have one stable API for detecting that Manager action is required instead of each app re-parsing the rejection shape, reason vocabulary, and user-facing instruction.

Issue: https://github.com/benjaminy/small-sea-collective/issues/119

## Current Context

The Hub returns `409 Conflict` from `POST /sessions/request` when a session request is well-formed but the Manager must provision or repair app state before the app can open a berth session.
The structured response contains `error: "app_bootstrap_required"`, `reason`, `app`, and `team`.
This body is not wrapped in `detail`.

The current `small_sea_client.client._check_response()` maps every `409` to `SmallSeaConflict`.
It first computes `detail = resp.json().get("detail", resp.text)`, then branches on status code.
That is appropriate for compare-and-swap cloud file conflicts, but it erases the structured app-bootstrap rejection from session callers.
The implementation therefore needs a small structural refactor:
parse the raw JSON response body once, inspect the top-level body for the app-bootstrap shape, and only then derive the generic `detail` fallback used by other errors.

The Hub spec currently lists these reason values:

- `app_unknown`
- `participant_berth_missing`
- `team_berth_missing`
- `app_friendly_name_ambiguous`

## Proposed Shape

Introduce a narrow app-bootstrap representation in `packages/small-sea-client`.
The public API will be an exception, matching existing client error siblings and preserving the existing non-error return shape of `request_session()`, `start_session()`, and `open_session()`.

`SmallSeaAppBootstrapRequired` should subclass `SmallSeaError`, not `SmallSeaConflict`.
App bootstrap is not a CAS conflict, and callers catching `SmallSeaConflict` for upload conflicts should not accidentally swallow Manager-provisioning failures.

The exception should carry:

- `reason: str`
- `app: str`
- `team: str | None`
- `user_message`, a property with canonical app-facing copy that tells the user to open Manager for the named app/team

`SmallSeaAppBootstrapRequired.__init__` should pass the rendered user-facing message to `super().__init__(...)`.
Anything logging `str(exc)` should see the same useful Manager instruction that an app would display, not an empty exception representation.

Canonical message wording:

- team present:
  `{app} isn't set up yet. Open Manager to register it for team {team}.`
- team absent:
  `{app} isn't set up yet. Open Manager to register it.`

Reason strings should be preserved raw rather than validated against a closed enum.
This follows the parent invariant from issue 111:
the response contract must stay stable enough that an app written today can keep using the same response codes after later branches add finer-grained reasons.

Keep CAS conflict behavior unchanged for non-bootstrap `409` responses.
Do not add compatibility shims beyond the clean API because the repo is pre-alpha.
The four current reason strings will appear in the Hub spec/server path and client micro tests.
Do not introduce a shared Hub/client constants module for this branch; that coupling would be heavier than the small deliberate duplication.

The parser should key on response body shape, not endpoint path.
Today only `/sessions/request` is expected to emit this rejection, but a body-shaped parser automatically covers any future endpoint that returns the same stable shape.
JSON parse failures must still fall through to `detail = resp.text` exactly as today.
Non-JSON error responses should continue to raise useful generic client errors rather than failing inside the parser refactor.

Current app scope looks narrow.
Shared File Vault uses `start_session()` in production code but does not have pre-existing app-bootstrap parsing, so the caller-search step is expected to be a no-op unless a later search turns up another caller.

## Implementation Steps

1. Read the existing `small-sea-client` API and nearby app callers to choose the least surprising public surface.
2. Refactor `_check_response()` to parse the raw JSON body before deriving `detail`.
   Detect top-level `{"error": "app_bootstrap_required", ...}` before generic `409` conflict handling.
3. Add `SmallSeaAppBootstrapRequired(SmallSeaError)` with preserved `reason`, `app`, `team: str | None`, and a `user_message` property.
4. Export the new exception from `small_sea_client.client`.
   Leave `small_sea_client.__init__` alone unless the package adopts public re-exports in this branch.
5. Add focused micro tests in `packages/small-sea-client/tests/test_client.py` for recognition, field preservation, formatting, raw reason preservation, and non-bootstrap conflict behavior.
6. Search app callers for hand-rolled app-bootstrap handling.
   Expected no-op given the current scan; if hits appear, absorb only trivial updates and record larger app-side wiring as follow-up work.

## Validation Plan

The validation needs to convince a smart skeptic of two things:
the branch accomplishes issue 119, and the repo remains consistent and maintainable.

### Goal Validation

- Add a micro test where `/sessions/request` returns `409` with `error: "app_bootstrap_required"`.
  Assert that the client raises `SmallSeaAppBootstrapRequired` with exact `reason`, `app`, and `team` values.
- Add a micro test for each supported reason value, or a parametrized test over the full current reason vocabulary.
  This guards against accidentally treating only `app_unknown` as special.
- Add a micro test for `team: null`.
  Assert that the exception preserves `team is None` and still produces useful Manager-oriented `user_message` copy.
- Add a micro test with an unknown future `reason`.
  Assert that the raw reason is preserved instead of rejected.
- Add a micro test showing that a response with bootstrap fields under `detail` is not required for recognition.
  The parser must inspect the raw top-level JSON body.
- Add a micro test proving the user-facing instruction includes Manager, the app name, and the team name.
  Assert the exact canonical sentence so apps can display this property verbatim.
- Add a micro test proving `str(exc)` uses the same Manager-oriented message as `exc.user_message`.
- Add a micro test for a `500` with a non-JSON body.
  Assert it still raises `SmallSeaError` with the response text, preserving the pre-refactor fallthrough behavior.

### Integrity Validation

- Keep the parser in `small-sea-client` only.
  Apps should consume the helper rather than learn Hub error internals.
- Assert `SmallSeaAppBootstrapRequired` is a sibling of `SmallSeaConflict` under `SmallSeaError`.
  This prevents CAS conflict handlers from catching bootstrap-required rejections by accident.
- Preserve existing `SmallSeaConflict` behavior for CAS `409` responses with dedicated regression micro tests:
  one `409` with JSON that has no top-level `error` key or a different `error` value, and one `409` with a non-JSON body.
- Run the targeted small-sea-client micro tests:
  `uv run pytest packages/small-sea-client/tests/test_client.py`
- If app callers are updated, run their package-level relevant micro tests as well.
  Prefer local mocked HTTP tests and avoid internet communication.
- Review `rg "app_bootstrap_required|SmallSeaAppBootstrapRequired" packages/` output after implementation to confirm no new duplicate parsing was introduced.
