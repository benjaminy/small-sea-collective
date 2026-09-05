Draw the Hub/Manager boundary around decisions, not file access

Issue 181 asked whether the Hub may read Manager-owned Core databases.
The answer settled on is that it may, and that the mandate should describe
authority over decisions rather than which process opens which file.

The Manager decides account registration, credential connect, replace and
disconnect, storage allocation, app registration and activation, and
membership.
The Hub reads NoteToSelf and team Core databases directly for its framework
responsibilities, and writes only where it holds the execution responsibility
that produces the result: a provider-issued locator for an allocation it did
not choose, a refreshed access token from its own provider I/O, and the
device-local cryptographic runtime state its encryption and decryption
advance.
Ordinary applications are unaffected: they still obtain identity and session
information through the Hub API and open no Core database.

Hub-owned cloud account registration is removed rather than kept as a
forwarding method.
`provisioning.add_cloud_storage` already wrote the same two rows, so the 29
call sites migrate as a parameter change.
Unknown-protocol validation moves to Manager registration, which rejects
before either database changes.
The accepted set is the Hub's four protocols plus `localfolder`, which
Manager callers depend on; adopting the Hub's list unchanged would have
narrowed the existing Manager API.

The four Core ORM models mirrored in the Hub are gone.
Three had no remaining references; the live `Nickname` lookup becomes a
parameterized SQL read, which also removes the unused engine that
`_find_participant` was returning to its callers.

AGENTS.md, architecture.md, the Hub spec, and the open architecture questions
carried the superseded exclusivity and never-writes claims and now carry the
default and its enumerated exceptions, plus the separate question of physical
schema ownership.
Two open items in that document — removing `/cloud_locations` and routing Hub
`open_session` through a Manager boundary — are resolved by the decision
itself.

Two exception-table questions went to a human rather than being decided here.
The audit found Hub writes reaching `device_local.db` through imported Manager
helpers, invisible as SQL in the Hub package; these broadened the cryptographic
runtime exception rather than becoming new ones.
`mark_admission_event_notified` is classified with sessions and sightings as an
ordinary Hub-local runtime record.

Validation: the Hub package passes whole (126), as do the migrated caller files
across five packages and four new Manager registration micro tests covering the
accepted protocol set, rejection before either database changes, that
registration makes no allocation decision, and that secret material stays off
the shared row.
Existing suites assert locator and cryptographic runtime writes.
A review follow-up adds mocked Hub token-refresh persistence micro tests for
Google Drive and Dropbox, verifying that only the allocated account's local
access token and expiry change.
Five Manager-package tests fail, none caused by this branch: four reproduce
identically with the branch stashed at `edc065e`, all inside `cod_sync/git.py`
on a merge path, and the fifth was a MinIO fixed-port collision between
concurrent pytest processes.
Details and exact commands are in the branch notes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013LzGZZYeZrqB3f1AbvsRz4
