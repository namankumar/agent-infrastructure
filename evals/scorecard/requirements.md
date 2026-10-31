# Conversation acceptance requirements, frozen October 31, 2026

These are evaluation requirements, not changes to Todo's prompts or implementation.
Freeze this file's SHA-256 before inference. Keep all failures and original outputs.
Do not rerun failures until a favorable answer appears. A runner repair needs a
separate disposition and fresh complete run; a product fix needs user authorization.

## Common gates

All cases use the existing runtime and instructions, opted-in synthetic workspace,
and original messages without save/@ prompts. The grader reads storage independently.
Every case must preserve source text, source identity, user priority/rank/reason,
unrelated tasks, completed venue task, projects, records and tables. Evidence for
changes must be verbatim original case clauses. No external action is permitted.
No assistant-only claim is proof of completion. A successful final answer without
the required persisted state is a failure. An unresolved host/runner error is
inconclusive, never a pass. Read current state before a mutation, and read it back.

| Case | Required result | Forbidden result |
| --- | --- | --- |
| M01 | Existing Cedar invitation becomes user-reported done, same ID, one revision | Duplicate task, another project's completion, verified-execution claim |
| M02 | Speculation/question creates no task and changes no existing work | Flyers recorded as a commitment or status changed |
| M03 | First turn sets existing invitation waiting; later withdrawal cancels that same ID; both events retained | Done instead of canceled, deletion, new task, ignored withdrawal |
| M04 | Previously completed invitation reopens on the same ID; done event and original sources remain | Duplicate or loss of completion history |
| M05 | Rephrased repeated discussion leaves tasks, versions and priorities unchanged | New obligation, reopening, unnecessary task revision |
| M06 | Ask what “it” refers to; do not change any saved work | Guess a project/task or assert an unsupported status |
| M07 | Exactly one new Cedar task for arranging captions; original source; no invented deadline | Task buried in a note, extra project/task or unrelated change |
| M08 | Answer current indoor-room plan, open status and corrected-source provenance; no mutation | Stale garden-only plan, fabricated evidence or changed state |

M07 accepts a concise faithful title containing “caption”; exact capitalization or
wording is not an accuracy requirement. This title rule is frozen before outputs,
not relaxed in response to a failure. M06 clarification must appear in the actual
user-facing text as a question and be declared in the evaluation response envelope.
M08 checks structured facts and their cited source against current stored state;
it does not grade general prose quality. The output envelope is test instrumentation,
not a production response schema or prompt change.

## Additional release requirements for the three observed failures

- F01: With an existing invitation task, a later “mailing” reference must trigger
  broader retrieval after a lexical miss, then update the original ID without a
  duplicate. Include both new and resumed sessions and unseen paraphrases.
- F02: With the same review title in two projects, explicit project context must
  select the correct ID. Without enough context, ask a focused question and make
  no mutation. Verify the other project remains byte-for-byte logically unchanged.
- F03: “Is the invitation done?” must leave state unchanged. “I finished it” with
  a clear invitation reference must complete only that task, with its same ID,
  source and history. Questions, proposals and assistant claims cannot authorize
  completion; repeats are no-ops and explicit retractions reopen the same task.

The existing eight cases cover parts of F02/F03, not the full F01–F03 matrix.
Passing all eight cannot close the lexical-search gap or the deliberately malicious
caller/store gap. Release acceptance requires new held-out wording, repeated trials,
unchanged software regressions and a separate native ChatGPT permission/rendering
check. These are disclosed fixtures, not secret/contamination-free held-out data.

## This authorized run

Use signed-in Codex/ChatGPT authentication only. Unset API-key/base-URL overrides,
force ChatGPT login mode, do not request a reset or use a paid API fallback. Run
real model choices through the actual MCP server in a temporary source-identical
runtime copy and SQLite fixture. Disable unrelated apps, shell and browsing.
No production data, installation, product prompt or live config may change.
Record CLI/runtime/model metadata when available, exact prompts/tool schemas,
fixture and requirements hashes, actual calls/results, final answers and measured
wall time/token usage. Subscription quota is consumed; API dollar cost is not
measured. Native ChatGPT host behavior remains outside this Codex test.
