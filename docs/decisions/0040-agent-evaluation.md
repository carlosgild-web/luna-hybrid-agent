# 0040: The agent evaluation

**Status:** accepted (2026-11-16)

## Context

PLAN_HYBRID.md Phase 7: implement section 5 (a gold set of about 30 questions in
tiers, tool-selection accuracy, value correctness, groundedness, iterations and
latency), run it with the fake LLM in CI and with real Ollama locally, then measure,
fix the worst failure mode and measure again. Done when the suite runs with one
command, reports the metrics table, and CI runs it with fakes.

## Decision

**The suite.** `backend/app/eval/agent_questions.yaml`, 30 cases: 7 doc, 9 db, 3
external, 6 hybrid, 5 refusal. Each case lists the tools it needs (a group of
alternatives counts once: `sales_report` or `sql_select`), the tools it must not call,
facts and numbers the answer must contain, the documents it must cite, and whether it
must refuse or draw a chart. The values come from the seeded business database and the
demo documents. `today` is fixed (2026-11-16), so "this year" doesn't move.

**Scored from the trace alone** (`eval/agent_suite.py`), with the answer check the chat
already shows (decision 0036):

- **numbers by value, not by SQL text.** 519725.51 is stated by "519,725.51",
  "519.725,51" or "519,726", and 59.8 by "60%". The rounding rule is the answer check's.
- **"right data fetched"** checks whether the expected numbers appear in any tool
  result. It doesn't fail a case. It separates "called the tool wrong" from "read
  the result wrong".
- **refusals** match a loose list of English and Spanish markers. A refusal that
  invents a number still fails the grounded check.
- **JSON or code in the answer** fails the case. This is the chart-as-JSON rate Phase 6
  asked for.
- The **LLM judge** is optional (`--judge`): the RAG judge's prompt over the run's tool
  results. It's a hint, as in decision 0019.

**Fake runs check the suite itself.** Each case has a `fake` script, a perfect run.
`agent-eval --fake` replays it with a scripted model against the real tools, in a
scratch data dir with the demo documents (fake embeddings: keyword search finds the
passages) and the seeded database. `{cite:<file>}` in a scripted answer becomes the
number of the passage the search really showed. So CI proves that every expected value
comes out of the real tools, and that the scorer passes a correct run. Separate tests
break runs on purpose (a forbidden tool, a made-up number, the wrong period, a chart as
JSON, a wrong citation, a limit hit) and check that the score names what broke.

**Commands.** `python -m app.cli agent-eval` (app stopped) runs Luna on Ollama. It
stores every trace, so the inspector opens any case, and it rewrites the report in
`DATA_DIR/eval/agent/` after every case. `--fake`, `--tier`, `--case`, `--judge` and
`--label` are options. `agent-eval-compare A B C` prints reports side by side and lists
the cases that changed. `agent-eval-rescore R` scores a report's stored traces again
with the current suite and answer check, without calling the model. External APIs are
always off in a run, so `fx_rate` answers with the stored rate and a run is
reproducible. CI runs `agent-eval --fake`, which fails on any failing case, and puts
the table in the run summary.

**PyYAML** is now listed in `requirements.txt`. It was already installed with
`uvicorn[standard]` and chromadb.

## The runs (qwen3:4b-instruct-2507-q4_K_M, 7.2 GB laptop, `OLLAMA_MAX_LOADED_MODELS=1`)

With one model loaded at a time, Ollama swaps qwen3:4b and bge-m3 instead of paging, and
no call hit the 300 s timeout that stopped Phase 6's hybrid runs. The reports are in
`docs/eval/agent/`. Their `trace_id`s point into the scratch data dir the runs used.

| Metric | Run 1 (19 of 30) | Run 2 | Run 3 |
|---|---:|---:|---:|
| Passed (all checks) | 53% | 53% | **67%** |
| Tool selection | 68% | 83% | 83% |
| Expected facts and numbers | 61% | 79% | 79% |
| Right data fetched | 89% | 93% | 93% |
| Grounded (no number from no tool) | 89% | 77% | 93% |
| Citations on the right document | 14% | 54% | 54% |
| Refused when it should | - | 100% | 100% |
| Charts drawn from tool data | 100% | 100% | 100% |
| JSON or code in the answer | 0% | 0% | 0% |
| Model calls per question | 1.79 | 1.80 | 1.80 |
| Seconds per question (median) | 43.5 | 58.5 | 75.4 |
| Seconds per question (max) | 308.3 | 273.6 | 284.8 |
| Passed: doc | 1/7 | 3/7 | 3/7 |
| Passed: db | 6/9 | 5/9 | 8/9 |
| Passed: external | 3/3 | 2/3 | 3/3 |
| Passed: hybrid | - | 1/6 | 1/6 |
| Passed: refusal | - | 5/5 | 5/5 |

All three are scored with the final suite and answer check (`agent-eval-rescore`).

1. **Run 1, baseline.** Claude Code stopped it at question 19 because the laptop ran
   low on memory. The 19 traces were stored, so the report was rebuilt from them.
   Later runs were started in a separate terminal, and the report is now written
   after every case. **Worst failure: 6 of 7 doc questions** were answered "I don't
   have any information about…" without a single tool call. The one that passed
   named its document ("the supplier agreement").
   **Fix 1 (prompt):** the hybrid rules now list what the documents cover and say to
   search them before saying something isn't there.
2. **Run 2.** Doc questions that searched went from 1/7 to 4/7, and citations on the
   right document from 14% to 54%. The pass rate didn't move: **a new worst failure,
   `[1]` after database facts, in 6 of 30 answers**, all in runs that showed no
   passage. Two failures were the measurement's fault, and both were corrected and
   rescored. `doc-returns` cited the FAQ, which states the same 30-day policy, so the
   case now accepts it. The answer check read the 1 of "1 MXN = 0.054054 USD" as a
   claim, and now treats it as a unit.
   **Fix 2 (code, not prompt):** in a run that showed no passages, a `[n]` points at
   nothing, so the loop removes it before the answer check and the chat
   (`drop_unbacked_citations`). The model's own text stays in its LLM step
   (invariant 11), and the check lists the removed markers. A run that did show
   passages keeps every marker, so a wrong number there is still `invalid`.
3. **Run 3.** DB questions 8/9, external 3/3, grounded 93%. The fix removed markers
   from 5 answers.

Fix 1 is a prompt change, so it's only as reliable as the model. Fix 2 is a rule, so
it holds for any model.

## Still failing in run 3 (10 of 30)

- **Doc questions answered without searching: 3** (resin gloves, carrier, the Spanish
  holiday question). The prompt rule isn't enough for a 4B model. The next step is to
  make the loop force one search when it is offered and the first answer says the data
  isn't there.
- **The wrong shelf: 1, a regression from fix 1.** "Payment terms in the supplier
  agreement" passed in run 1. Since run 2, Luna searches with `shelf: meetings` and
  finds only the meeting notes. The prompt's new list of document kinds seems to pull
  her toward a shelf. Searching every shelf when the one chosen has no good match
  would fix it.
- **Hybrid questions answered from one source: 2.** The worst answer in the set:
  "Was the acorn charm order received late?" got **"No"** from the meeting notes
  alone, while the purchase records say *received late*. The check passes every number
  in it, because the error is in the reasoning. Only the tool-selection score catches
  it.
- **A hybrid answer without a citation: 1**, and **one with citations to passages
  never shown: 1** (the documents were searched, so the markers stay and count as
  invalid).
- **Arithmetic: 1.** "The difference is 6,925.51 MXN" is the model's own subtraction,
  which invariant 12 forbids. A tool for the difference, or a prompt rule against
  computing it, are both open.
- **"Top 5 by units" ranked by revenue: 1.** Luna calls `sales_report` without
  `order_by: units`, then says so in the answer. A tool description that says when to
  use `order_by` is the next fix to measure.

## Consequences

- A full real run takes about 50 minutes on this laptop (median 75 s per question,
  doc and hybrid questions 2½–3½ minutes each), and needs Chrome and similar apps
  closed.
- The suite is the regression test for prompt changes: run 2 shows a prompt fix can
  break a case that passed before (`doc-supplier-terms`).
- The open items above are the input for any later tuning. Phase 8 puts this table and
  its story in the README.
