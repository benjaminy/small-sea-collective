# One Participant, Two Devices

This note describes how one person's second device joins a team and reads and writes the same Files niche as the first.
The micro test `tests/test_files_two_device_capstone.py` runs exactly this path, with two installations, two Hubs, and one MinIO standing in for the person's cloud provider.
Manager-side details live in the [Manager spec](../../small-sea-manager/spec.md); this note links there instead of repeating them.

## Who does what

The Manager makes every management decision: the cloud account, the team, Files registration and activation, and the storage routes for the Core and Files berths.
Files never opens a Manager-owned database.
It learns its team, berth, and identity from the Hub's session API.
Each device's Hub makes all of that device's cloud calls; the test checks that every provider call came from the right device's Hub.

The only things a person carries between devices are four artifacts: the identity-join request and welcome, and the linked-team join request and bootstrap response.

## What NoteToSelf shares, and what stays on each device

Shared NoteToSelf (`NoteToSelf/Sync/core.db`) carries the participant, their devices, the list of teams, registered apps, cloud account locators (protocol and URL), berth allocations, and the public team-device keys.
After device B refreshes NoteToSelf, it knows the team exists but has not joined it locally.

Cloud credentials do not travel.
They live in the device-local database (`NoteToSelf/Local/device_local.db`), so B must enroll its own credentials for the synced account with `connect_cloud_storage_credentials`.
Private keys and sender-key state also stay local; B redistributes its sender key to A after joining.
See the [Manager spec](../../small-sea-manager/spec.md) for the full table lists.

## How the team baseline reaches device B

B cannot decrypt Core history that was published before B was authorized (#280).
So device A sends the baseline directly.
A's signed, encrypted bootstrap response names the Core berth and Core head, and carries a git bundle of Core `main` at that head.
B checks the signer against the team-device keys in NoteToSelf, imports the bundle into a fresh clone, and discards the clone if its head, berth, or certificates disagree with what A signed.
This is a cold start, not Core integration.
The details are in [Linked-device team bootstrap](../../small-sea-manager/spec.md#linked-device-team-bootstrap-into-an-existing-team).

## Self-store refresh versus teammate fetch

Both devices act as the same teammate, so they share one Files store in the cloud.
B reads it with `fetch_self_via_hub`, not the teammate fetch `fetch_via_hub`.

- `fetch_self_via_hub` reads the participant's own registry and, if named, one niche. It parks the fetched heads and leaves the checkout alone. `merge_self` then integrates them.
- `fetch_via_hub` reads another teammate's registry and niche, named by `from_teammate_id`, and `merge_via_hub` integrates them.

On a fresh device, B first fetches only its own registry, merges it to discover the niche names, and then fetches the niche.
After B publishes and pushes a change, A picks it up with the same `fetch_self_via_hub` and `merge_self`, and A's next publish proceeds without a conflict.

## Known gaps

- Files commits and publications are unsigned (#266). A fetched head proves what the store held, not which device wrote it.
- #190's signed-history verification of the bootstrap baseline is not wired up. B checks who signed the head, but not who authored the commits behind it.
- B cannot read team history published before it joined (#280); the bundle works around this only for Core.
- Nothing bounds the size of the Core bundle in the bootstrap response.
