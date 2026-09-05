# GitHub follow-up

After implementation, prepare a #249 update describing the single shared MinIO fixture in `test_support.py`, dynamic API and console ports, endpoint propagation, and exact concurrent validation results.
Explain that this branch also covers real Hub test listeners needed for concurrent test execution.
State that sandbox changes were split out of this branch.

Do not close #249 until the two-process MinIO acceptance check passes.
Record concrete additional defects discovered during implementation as focused issue proposals, with reproduction evidence and their relationship to this branch.

Prepare a separate sandbox issue proposal covering fixed-port collisions in both fresh and saved workspaces.
Include reproduction evidence using disposable workspaces and identify port-based server identity and Manager-configured S3 URLs as design considerations.
Consider stable server IDs with ports allocated at startup, but resolve how stored endpoint consumers remain usable before choosing the design.
Do not treat the README's stable-port description as a requirement or impose compatibility with scratch artifacts without a concrete need.
Existing developer workspaces must not be treated as disposable during validation.
The human handles posting the issue proposal; sandbox implementation and validation do not block #249.

## Defects found during implementation

### ntfy test fixture binds a fixed host port (new issue proposal)

`packages/small-sea-hub/tests/conftest.py`'s `ntfy_server` fixture runs
`docker run -p 9090:80`, a fixed host port, so two concurrent pytest processes cannot both start it.
Reproduction: run `packages/small-sea-hub/tests/test_notifications.py` in two simultaneous pytest processes.
One passes; the other errors during fixture setup with
`Bind for 0.0.0.0:9090 failed: port is already allocated`,
and leaves a created-but-not-started `ntfy-test-<pid>` container behind, since the failure path never removes it.

Relationship to #249: the same class of defect, on a service the #249 branch scoped out.
The MinIO and Hub work does not fix or worsen it.
A fix would allocate the host port dynamically and remove the container on a failed start.
