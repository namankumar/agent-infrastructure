# Offline comparison: October 28, 2026

**Partial coverage; product failures retained.** This is a repeatable comparison of
existing retrieval configurations and runtime contracts, not a model-accuracy or
production-readiness claim. No application code, prompts, policies, routing or live
data were changed for these results.

## Scorecard

| Measure | Result | Interpretation |
| --- | --- | --- |
| Planned / reached cases | 26 / 18 | 18/18 software scenarios reached; 8/8 model scenarios not reached |
| Software trials | Three per scenario/configuration | 30 retrieval runs plus 39 contract probes = 69 |
| Valid runs | 69/69 (100%) | A measured failure is valid evidence, not a successful goal |
| Runtime contracts | 36/39 pass | Twelve scenarios × three pass; S13 fails three times |
| Unsupported semantic mutation | Accepted 3/3 times | Existing runtime accepts completion with question-only evidence; **fail** |
| Current fact + original/revision evidence: full | 15/15 | Deterministic structured oracle, not a model's final answer |
| Current fact + original/revision evidence: scoped | 12/15 | R04 synonym miss loses the required fact in all three trials |
| Unrelated-item exclusion: full / scoped | 0/15 / 12/15 | Full intentionally returns all work; scope is not tenant authorization |
| Combined retrieval criteria | 9/30 pass | Nine scoped passes; includes deliberate full-context exclusion failures; not an accuracy headline |
| Model goal / final-answer success | **Not reached** | No model/provider invoked, no generated predictions scored |
| Cost / tokens / operational impact | **Not measured** | No inference, billing or productivity claim |
| Native ChatGPT acceptance | **Not rerun / historically partial** | Safety/write and reply restoration failures remain explicit |

## Full context versus existing subject scope

Each pair uses byte-identical copies of a seed containing two synthetic projects,
one corrected task, a completed task, a manual priority and unrelated repeated text.
All tool calls use the genuine current implementation. Prompt/model/tool behavior
was not experimentally changed. See [results.json](results.json) for bindings,
per-run checks, seed hashes, exact local tool timing and evidence projections.

| Task | Full median bytes | Scoped median bytes | Reduction | Required current fact, full / scoped |
| --- | ---: | ---: | ---: | --- |
| R01: current invitation status | 87,079 | 9,022 | 89.64% | 3/3 / 3/3 |
| R02: completed venue task | 87,079 | 9,022 | 89.64% | 3/3 / 3/3 |
| R03: corrected room detail | 87,079 | 9,022 | 89.64% | 3/3 / 3/3 |
| R04: synonym “mailing” | 87,079 | 631 | 99.28% | 3/3 / **0/3** |
| R05: ambiguous “review” | 87,079 | 86,978 | 0.12% | 3/3 / 3/3; unrelated project remains |

R01–R03 ask different facts of the **same fixture**; they are not three independent
workloads. No overall payload-improvement generalization follows. R04 demonstrates
why a smaller response can be worse. R05 shows that a subject query can preserve
the answer while barely reducing context. The earlier 96.91% result was a separate
single fixture; it is not pooled with this dataset.

Local `perf_counter_ns` durations include actual tool dispatch/store work, exclude
model/network/rendering, and are recorded per run. Sequential full-first ordering,
small sample count and filesystem caches prevent a credible latency-gain claim.
No cost estimate is inferred from bytes.

## Retained failures and proposals: implementation on hold

1. **R04, fail:** lexical matching does not retrieve a synonym absent from stored
   text. Existing guidance already requires broader matching after a miss. A future
   evaluation should compare that existing multi-step workflow with actual model
   traces before proposing a new retrieval implementation.
2. **R05, scope failure:** ambiguous wording returns both projects. A future model
   test should check clarification or explicit project selection, rather than assume
   scope is an authorization boundary.
3. **S13, fail:** a deliberately incorrect tool caller supplies `status: done` with
   only “Is the Cedar invitation done?” The runtime accepts it and increments the
   existing task version. The evidence has valid shape but does not authorize the
   semantic change. This is **not** proof that ChatGPT makes this mistake in practice;
   it is proof the runtime alone cannot prevent it. Any remediation requires separate
   product approval; none was applied.
4. **S10 initial run, harness error:** three inconclusive calls used an unsupported
   table-create shape. The fixture was corrected to the already-existing API, and all
   three reruns pass. [Grader investigation](grader-investigation.json) retains cause
   and disposition. No application change was made to pass the test.

## Model comparison and held-out work

M01–M08 define actual conversation judgments, expected state and source bindings,
including questions, resumed references, withdrawals, delayed corrections, repeated
work and correct task filing. Six are reserved as held-out **within this run** and
have not been executed. They are publicly disclosed, not a future contamination-free
benchmark. A trace-replay adapter can score genuine externally captured baseline and
candidate outputs, but absent traces are **not-reached**, never counted as zero
failures or successful interpretation.

No baseline-versus-candidate prompt/model/version improvement has been measured.
The only executed A/B comparison is full versus scoped reads in the same runtime.
Prompt/policy file hashes are bound for reproduction; they are evidence metadata,
not a new prompt rollout. Read [the method and trace format](README.md).

## Evidence and verification boundary

- [Cases](cases.json), [runner/graders](run.py), [machine scorecard](results.json),
  and `tests/test_scorecard.py` belong to the main project, not a parallel app.
- Existing requirements: capture/source evidence, correction/version safety,
  persistence, manual-priority preservation, context scope, evolving tables and
  resumed-conversation behavior. This evaluation narrows evidence for those existing
  requirements; it does not change their implementation or acceptance status.
- Existing Python suites cover genuine store, MCP, HTTP, OAuth/PKCE, refresh replay,
  restart and event-adapter contracts using isolated storage/test clients. Passing
  transport tests are not native-host acceptance or multiuser security evidence.
- This run uses no private conversation fixtures. Historical real-conversation
  ingestion and model extraction receipts remain dated private-project evidence;
  they are not republished, rerun or silently counted as this synthetic model score.
- No cloud migration, deployment, production configuration, credentials, accounts,
  live databases or plugin installation was changed. No paid inference, background
  scan, outreach, recording or external service call is part of the runner.

The safe public export contains genuine implementation modules plus this synthetic
evaluation, not private source-repository history. No license was added. Fresh-copy
privacy review and fresh-clone execution are required before publishing the update.
