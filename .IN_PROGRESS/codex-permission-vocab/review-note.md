# Review Note

This documentation-only branch separates decentralized teammate integration from real local Hub authorization.
It defines automatic and proposal-only as a deliberately shallow involvement model for medium-sized teams, makes complete signed teammate history part of every Core snapshot, and keeps the full Git commit DAG while allowing old bulk blobs to dehydrate.
It also preserves strict device non-impersonation through prepared recovery, makes unprepared recovery a new-identity path, and treats signed clone-staleness observations as warning evidence rather than silent finality.
Current schema/UI names remain implementation vocabulary pending issue #162 and the recorded recovery, retention, and checkpoint follow-ups.
