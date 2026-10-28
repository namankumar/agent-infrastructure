# Agent Work Continuity

A tiny, offline Python example of how an agent workflow can preserve durable, source-backed work state without treating a prior chat response as current truth.

It demonstrates four implemented controls:

- **Source-backed state:** every recorded work item retains a source ID, exact quote, and SHA-256 hash of that quote.
- **Stale-write rejection:** callers must supply the state revision they read; an old writer cannot overwrite a newer revision.
- **Replay safety:** repeating the exact same request ID returns the original immutable receipt. Reusing that ID with changed content is rejected.
- **Current verification vs. historical receipts:** mutations return historical receipts (`receipt_only: true`). `status` performs a new database read and is explicitly not a mutation receipt.

`review` also requires a reviewer label distinct from the recorded owner. It is a small separation-of-duties example—not user authentication or production authorization.

No network, API key, model, package install, or workspace dependency is needed. All names and inputs are synthetic.

## Run

Requires Python 3.9+.

```sh
mkdir -p /tmp/agent-work-continuity-demo
python3 -m agent_continuity.cli --db /tmp/agent-work-continuity-demo/state.sqlite record --input examples/record.json
python3 -m agent_continuity.cli --db /tmp/agent-work-continuity-demo/state.sqlite status routing-contract
python3 -m agent_continuity.cli --db /tmp/agent-work-continuity-demo/state.sqlite review --input examples/review.json
python3 -m agent_continuity.cli --db /tmp/agent-work-continuity-demo/state.sqlite recover
```

The first two mutations return receipts with the revision they wrote. The `status` output is the current database read; it includes the saved source reference and review history.

To demonstrate safe replay, rerun the exact `record` command after recording it: it returns the original receipt and does not increment the revision. To demonstrate stale-write rejection, try `examples/review.json` before recording, or change its `expected_revision` to `0` after the record is present.

## Inputs and outputs

`record` accepts one JSON object:

```json
{
  "item_id": "routing-contract",
  "expected_revision": 0,
  "idempotency_key": "record-routing-v1",
  "actor": "planner",
  "summary": "Keep the routing contract source-backed and reviewable.",
  "source": {
    "source_id": "demo-note-001",
    "quote": "Keep the routing contract source-backed and reviewable.",
    "sha256": "SHA-256 of quote"
  }
}
```

`review` has the same common fields plus `verdict` (`verified` or `needs_changes`) and a source object. Its `actor` must differ from the item owner.

Each successful mutation returns an immutable historical receipt:

```json
{"item_id":"routing-contract","operation":"record","receipt_only":true,"revision":1,"state":"recorded"}
```

Use `status ITEM_ID` before a new write. That is what supplies the current revision to place in `expected_revision`.

## Tests

```sh
python3 -m unittest discover -s tests -t . -v
```

The suite covers a source reference surviving persistence, exact replay, changed replay rejection, stale-write rejection, self-review rejection, source hash rejection, and SQLite recovery receipts.

For a clean-directory check:

```sh
tmpdir="$(mktemp -d)"
cp -R . "$tmpdir/agent-work-continuity"
cd "$tmpdir/agent-work-continuity"
python3 -m unittest discover -s tests -t . -v
```

## Architecture and provenance

See [the architecture diagram](docs/architecture.md) and [the public export boundary](docs/provenance.md).

## Limits

This is intentionally narrow. It does **not** authenticate people, validate authority over a real source system, watch files continuously, discover all relevant work, execute agents, call models, provide multi-user access control, or recover a corrupt database automatically. The source hash protects consistency of the submitted quote, not the authenticity of an external message.

`recover` reports SQLite's integrity check and the recovery action required if it fails. A real deployment needs encrypted backups, identity/authentication, authorization at its service boundary, and source-specific freshness checks.
