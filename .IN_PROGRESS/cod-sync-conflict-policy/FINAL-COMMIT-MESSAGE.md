Define bounded Cod Sync publication settlement and caller recovery

Freeze one attempted head per publication, limit settlement to one head write and two validated observations, and surface typed published, already-present, retryable, integration-required, and unresolved outcomes.
Use etags as settlement evidence, make local head visibility atomic, and park divergent heads without changing application state.

Translate the result contract through Manager and `ssc-files`, including explicit restart-safe `ssc-files` integration for a participant's own competing registry and niche heads.
Document the remaining Manager, Hub outcome-classification, storage-etag, and response-loss work as focused follow-ups.
