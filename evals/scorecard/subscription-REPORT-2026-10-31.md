# Subscription model acceptance: October 31, 2026

**24/24 case runs pass: all eight cases, three fresh trials each.** The independent
frozen-requirements audit also passes 24/24. There were zero failed or inconclusive
cases in the accepted batch. This is current Codex model behavior through actual
Todo MCP tools, not a claim that the earlier software failures were fixed.

| ID | Requirement | Passed |
| --- | --- | --- |
| M01 | Complete the existing task | 3/3 |
| M02 | Do not turn speculation into a task | 3/3 |
| M03 | Waiting, then withdrawal/cancellation | 3/3 |
| M04 | Reopen after a delayed correction | 3/3 |
| M05 | Repeated discussion creates no duplicate/revision | 3/3 |
| M06 | Clarify an ambiguous reference | 3/3 |
| M07 | Capture a real commitment as a task | 3/3 |
| M08 | Answer from corrected current facts | 3/3 |

## What actually ran

The signed-in Codex CLI made the tool calls itself. Each case used a temporary,
source-identical copy of the existing runtime and synthetic SQLite fixture; no
model-generated mutation was supplied or executed by the grader. CLI sessions were
fresh; M03 delivered waiting and withdrawal as separate sessions sharing the same
test database and prior conversation. Golden outcomes/graders were not in the model
prompt or temporary runtime. The existing skill and policy were supplied unchanged.
A generic output envelope collected user-facing text and factual citations; it was
not installed into the product.

The runner forced ChatGPT authentication, removed API-key/endpoint environment
overrides and loaded no user provider config. No API key, paid API fallback, usage
reset or production database was used. This consumes subscription quota; no dollar
cost is claimed. CLI version: `codex-cli 0.165.0-alpha.1.2`. Model selection was the
CLI default; a concrete model slug was not emitted in the captured JSON/stderr, so
this receipt does not establish a pinned-model benchmark or future default identity.
Runtime version and tool/prompt/code hashes are in the machine report.

There were 27 accepted CLI turns across the 24 case runs. Measured summed turn wall
time was 372.528 seconds; it is not a latency comparison. Reported token usage was
3,172,905 input tokens (including 2,333,440 cached input tokens) and 6,834 output
tokens. These CLI counters are not dollar charges or a measured productivity gain.

## Strict verification

The [requirements](requirements.md) were frozen before inference; every case binds
the same requirements hash. Independent storage inspection verifies expected status,
stable IDs, exact revisions, source text/evidence, manual choice preservation,
unrelated completed work and project preservation. Actual call traces prove reads
before and after writes. No note/table writes were made in place of tasks. M08
includes current indoor-room detail and source provenance rather than the stale
plan. Title wording in M07 is graded for faithful caption-task content, following
the pre-run requirement, rather than an arbitrary exact title string.

All bound runtime/policy/fixture hashes still match after execution. The main
software suite passes **188 tests**, including seven new negative grader tests.
The offline software scorecard was rerun separately: its lexical miss, ambiguous
scope and S13 unsupported-status mutation remain failures. Nothing was remediated.

## Initial diagnostic and permission boundary

The initial M01 diagnostic chose the correct task and update but the CLI refused
the write because its noninteractive approval policy had no write-tool grant.
The assistant stopped and accurately reported that nothing was saved. That attempt
is retained as [diagnostic evidence](subscription-diagnostic.json), not hidden or
counted as a successful capture.

For the complete batch, the supported CLI configuration granted only named tools
for the explicitly authorized synthetic test database, scoped to that ephemeral
invocation. No installed plugin, host-global permission or product tool annotation
changed. The complete batch was then run with three predetermined trials per case;
failures were not selectively rerun until green.

## What remains open

Passing these eight does **not** close the [broader failure-specific requirements](failure-acceptance-cases.json):
lexical/synonym recovery, project ambiguity across more variants, question/completion
sequences and held-out wording still need their dedicated matrix. Six original
cases had been reserved but were already publicly disclosed; this is not an
uncontaminated held-out benchmark. Three repeats are limited evidence, not a
reliability percentage across real users or long conversations.

The S13 store-level gap is still present: an erroneous caller can submit completion
with question-only evidence. This batch shows correct model choices in the eight
controlled scenarios; it does not add runtime semantic enforcement. Native ChatGPT
permission rejections, response restoration, hosted OAuth and closed-chat capture
are not tested by local stdio Codex runs. No product changes were made.

## Reproduction and evidence

- [Requirements](requirements.md), [runner](run_subscription.py), [independent auditor](audit_subscription.py).
- [Full synthetic case results/tool calls/answers](subscription-results-2026-10-31.json).
- [Independent audit](subscription-audit-2026-10-31.json).
- Raw exact prompts/events/stderr remain under ignored `data/evals/subscription-2026-10-31/`;
  they are not a public export or a source of personal production data.
- Run commands and subscription safeguards are in [README](README.md#october-31-subscription-run).
