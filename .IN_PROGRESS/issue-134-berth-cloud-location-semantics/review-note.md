# Review Note

This branch is a design/spec branch for #134.
It updates the architecture, Hub spec, Manager spec, and branch plan to define Manager-owned berth cloud location semantics.

The important review questions are:

- Is the separation between cloud account locator, device credential, local berth allocation, and member-berth storage announcement clear?
- Is the Manager-decides / Hub-reconciles-provider-reality contract precise enough?
- Are missing allocation, missing credentials, provider user action, materialization failure, and allocation conflict distinguishable enough for implementation?
- Does the `(member_id, berth_id)` peer routing model cover teammate and same-member sibling-device cases?

No broad production code was changed in this branch.
Implementation is intentionally split into follow-up slices.
