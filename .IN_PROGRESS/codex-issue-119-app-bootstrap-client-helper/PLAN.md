# Issue 119: App Bootstrap Client Helper

## Goal

Add a `small-sea-client` helper for Hub `409 app_bootstrap_required` responses.
Apps should have one stable API for detecting that Manager action is required instead of each app re-parsing the rejection shape, reason vocabulary, and user-facing instruction.

Issue: https://github.com/benjaminy/small-sea-collective/issues/119

## Current Context

The Hub returns `409 Conflict` from `POST /sessions/request` when a session request is well-formed but the Manager must provision or repair app state before the app can open a berth session.
The structured response contains `error: "app_bootstrap_required"`, `reason`, `app`, and `team`.

The current `small_sea_client.client._check_response()` maps every `409` to `SmallSeaConflict`.
That is appropriate for compare-and-swap cloud file conflicts, but it erases the structured app-bootstrap rejection from session callers.

The Hub spec currently lists these reason values:

- `app_unknown`
- `participant_berth_missing`
- `team_berth_missing`
- `app_friendly_name_ambiguous`

## Proposed Shape

Introduce a narrow app-bootstrap representation in `packages/small-sea-client`.
Likely pieces:

- a `SmallSeaAppBootstrapRequired` exception or result helper carrying `reason`, `app`, and `team`
- an exported predicate or formatter if the surrounding app code wants to handle the state without catching a broad conflict
- a canonical user-facing instruction that tells the user to open Manager for the named app/team

Keep CAS conflict behavior unchanged for non-bootstrap `409` responses.
Do not add compatibility shims beyond the clean API because the repo is pre-alpha.

## Implementation Steps

1. Read the existing `small-sea-client` API and nearby app callers to choose the least surprising public surface.
2. Update `_check_response()` so the app-bootstrap rejection is detected before generic `409` conflict handling.
3. Export the new helper/exception from `small_sea_client.client` and, if the package starts exporting symbols later, from `small_sea_client.__init__`.
4. Add focused micro tests in `packages/small-sea-client/tests/test_client.py` for recognition, field preservation, formatting, and non-bootstrap conflict behavior.
5. Search app callers for hand-rolled app-bootstrap handling and update any direct duplication found in scope.

## Validation Plan

The validation needs to convince a smart skeptic of two things:
the branch accomplishes issue 119, and the repo remains consistent and maintainable.

### Goal Validation

- Add a micro test where `/sessions/request` returns `409` with `error: "app_bootstrap_required"`.
  Assert that the client raises or returns the new stable helper type with exact `reason`, `app`, and `team` values.
- Add a micro test for each supported reason value, or a parametrized test over the full current reason vocabulary.
  This guards against accidentally treating only `app_unknown` as special.
- Add a micro test for missing optional fields or malformed bootstrap-shaped details if the chosen API supports a graceful fallback.
  The expected behavior should be explicit rather than discovered later by app authors.
- Add a micro test proving the user-facing instruction includes Manager, the app name, and the team name.
  This verifies the reusable copy is app-ready, not merely machine-detectable.

### Integrity Validation

- Keep the parser in `small-sea-client` only.
  Apps should consume the helper rather than learn Hub error internals.
- Preserve existing `SmallSeaConflict` behavior for CAS `409` responses with a dedicated regression micro test.
- Run the targeted small-sea-client micro tests:
  `uv run pytest packages/small-sea-client/tests/test_client.py`
- If app callers are updated, run their package-level relevant micro tests as well.
  Prefer local mocked HTTP tests and avoid internet communication.
- Review `rg "app_bootstrap_required|SmallSeaConflict|409"` output after implementation to confirm no new duplicate parsing was introduced.

## Open Questions

- Should the stable API be an exception only, or should there also be a small formatting helper for apps that want to display the Manager instruction consistently?
- Should unknown future reason strings be preserved as raw strings, or should the helper validate against the current vocabulary and raise a generic client error for unknown values?
