Allocate test service ports dynamically

Independent pytest processes shared hardcoded MinIO and Hub ports, so concurrent runs collided.
Port allocation now belongs to the fixtures, which pass the resulting endpoint to consumers.
The five MinIO launchers share one fixture with independently allocated API and console ports, loopback binding, and S3 readiness authenticated with credentials unique to each launch.
This rejects unrelated test servers that take the port after reservation.
The root Hub fixture also allocates dynamically, and the sync roundtrip test uses its readiness probe while preserving session auto-approval.

Allocation remains advisory because reservation sockets close before the service binds.
Occupied explicit ports fail before launch, MinIO readiness failures clean up the child and owned directory, and Hub allocation failures create no temporary root.
Micro tests exercise collisions, instance isolation, and failure cleanup; local integration and concurrent runs cover endpoint propagation.
