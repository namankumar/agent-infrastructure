# Architecture

```mermaid
flowchart LR
    P[JSON mutation request] --> V[Validate shape, source quote hash, and expected revision]
    V --> T[SQLite BEGIN IMMEDIATE]
    T --> I{Idempotency key seen?}
    I -- same canonical request --> R[Return original immutable receipt]
    I -- new key --> C{Expected revision current?}
    C -- no --> X[Reject stale write]
    C -- yes --> S[(Persist source-backed state + revision)]
    S --> O[(Persist immutable operation receipt)]
    O --> R
    Q[status] --> S
    Q --> N[Current read: not a historical mutation receipt]
```

The source object is retained with each state revision. A write carries a caller-supplied `expected_revision`; the store serializes writers with SQLite and rejects a write based on a state snapshot that has since changed. A repeated request returns its first receipt exactly, while `status` performs a new database read.

`review` additionally requires the reviewer identity string to differ from the recorded owner. That is a small separation-of-duties control, not user authentication or an access-control system.
