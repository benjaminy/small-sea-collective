Open questions for the next removal slice:

- Should `receive_sender_key_distribution` and the Hub's `_process_runtime_inbox_from_teammate` accept a removed sender's authentic redistribution artifacts?
  They read historical certificates and authenticate incoming senders; they do not choose future recipients.
  This branch leaves them unchanged because their bootstrap and retained-history policy needs a decision.
- How should pending anchor removal appear in membership status?
  The requested three states depend only on effective and disputed removals, so a pending-only anchor removal leaves status active.
  Active here does not promise unambiguous authority or complete evidence.
- Should a local removal whose outcome is disputed still rotate keys, clear receiver state, and display the existing success notice?
  Those existing side effects remain; this branch only changes evidence retention and current-reader exclusion.
  Imported removal reconciliation also lacks the local removal action's receiver-state cleanup.
- Should disputed targets stop receiving keys?
  This branch excludes only effective removals, as requested.
  Admission snapshots/quorum, device linking, pre-anchor reads, and peer-existence checks retain their current policy.

No GitHub issue changes were made.
These questions need an owner decision before a broader reader or runtime change.
