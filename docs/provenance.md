# Provenance and export boundary

This is a deliberately small, synthetic public example. It was distilled from two private local implementation families:

- a SQLite change-verification tracker that uses `BEGIN IMMEDIATE`, expected revisions, immutable idempotency receipts, and explicit current-read vs. historical-receipt semantics;
- a persistent record store that retains source references and revision snapshots, rejects stale writes, and rejects request-ID reuse with changed content.

Only these general mechanisms were reimplemented here. No client workflow, client name, source path, source record, prompt, credential, data schema, service configuration, or hosted integration was copied. The inputs, actors, sources, and work items in this repository are fictional.

The original systems have broader source verification and lifecycle controls. This example intentionally does not claim to reproduce them.
