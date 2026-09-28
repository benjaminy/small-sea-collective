Starting point: `/Users/benjaminy/Repos/ssc-wt-dag`, branch `t75-narrow`, commit `6c40d837cc6724ee0a3a670be1ed74961ef6346f`.
The working tree was clean.
Read removal-design.md section 5 and the task 75 implementation note from the supplied scratchpad path.
No code or commits came from `t75-keep-evidence`.

The evidence tables remain unfiltered.
`get_current_recipient_device_keys_by_teammate` filters only effective removal targets and serves redistribution and reconciliation.
Historical key readers remain unchanged.
`provisioning.list_teammates(..., include_removed=True)` exposes retained members with removal status; the default listing excludes removed teammates.
The membership UI and CLI show the status of listed teammates.
An effective removal takes precedence if a target also has a disputed removal.

The evidence regression retains every existing row in the event, teammate, device, certificate, delegation, mode, and four admission tables.
It includes a signed proposal authored by the removed teammate to exercise the cascade risk.
The replay regression covers all six permutations of three signed events, duplicate insertion while both pending and stored, and a certificate arriving after removal.
It compares the complete resulting authority views, including their identifiers.
Additional micro tests cover status independence from mode ambiguity, pre-anchor/unsupported-policy fallback, and reconciliation after retained removal evidence arrives.

The sandbox forbids localhost socket binding, so MinIO-dependent checks cannot pass here.
Use `UV_CACHE_DIR=/private/tmp/ssc-t75-uv-cache uv run --offline pytest -q` to avoid the unwritable default uv cache in this session.
The focused run across sender-key, admission-record, authority, and membership-route tests passed 72 checks and failed one at MinIO port allocation (`Operation not permitted`), before bootstrap code ran.
`git diff --check` passed.

Staging is also blocked by the sandbox.
`git add` could not create `/Users/benjaminy/Repos/small-sea-collective/.git/worktrees/ssc-wt-dag/index.lock` (`Operation not permitted`).
That worktree metadata directory is outside the writable roots.
No commit was attempted because the required checks have not passed; no commit was amended.

Manager package run: 574 passed, 24 failed, 13 errors in 208.70 seconds.
The later-added reconciliation regression is included in the focused run above.
The complete Manager log is `/private/tmp/t75-manager-tests.log`.
All 37 Manager failures/errors report the same localhost bind denial at `test_support.reserve_ports`; none reports an assertion failure in the changed behavior.
The CLI membership status smoke check passed using Click's runner and a disputed-member fixture.

Full suite: 1,350 passed, 3 skipped, 58 failed, 57 errors in 366.39 seconds.
Log: `/private/tmp/t75-full-tests.log`.
All 115 failures/errors trace to sandbox-denied socket operations.
One readiness test reports a regex mismatch because it expected an exited-server/readiness error but received the port-bind denial instead.
The full-suite collection preceded the final reconciliation micro test and CLI display edit; those passed the focused run and CLI smoke check respectively.

Work remains uncommitted and unstaged on `t75-narrow` at the starting commit.
The source change is ready for unrestricted validation, then the authorized commit.
