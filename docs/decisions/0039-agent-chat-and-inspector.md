# 0039: The agent in the chat, and the inspector

**Status:** accepted (2026-11-16)

## Context

PLAN_HYBRID.md Phase 6: agent mode in the chat (console and/or the Luna portal), SSE
streaming of the steps as they happen, chart rendering with recharts, and an agent
inspector that rebuilds every step of an answer from the stored trace. Until now, the
agent ran only from the CLI, and its traces weren't linked to chat messages.

## Decision

- **Its own endpoint.** `POST /api/agent/chat` (`question`, `session_id?`,
  `channel`), next to `POST /api/chat`, instead of a mode flag on the RAG endpoint. The
  two paths share sessions and messages but nothing else: the RAG chat streams tokens
  from one prompt, while the agent streams steps from a loop. Events: `session`, one `step`
  per model call or tool call as it ends, `answer` (text, sources, check, charts),
  `done` (`message_id`, `trace_id`). A failed model run sends `error` with the
  `trace_id` instead.
- **The loop runs in its own thread.** The stream reads the steps from a queue fed by
  `run_agent`'s `on_step`. A client that leaves doesn't stop the run: the trace and
  the answer are saved anyway (invariant 8), and the chat finds them in the session.
- **A model step opens the tool chip.** The loop already emits a model step with the
  tool calls it asked for before running them, so the UI shows "🔧 sales_report…"
  without a new event type. The streamed steps leave out tool results. The inspector
  reads them from the stored trace (invariant 11).
- **Messages point to their run.** Migration 0013 adds a nullable
  `messages.agent_trace_id`. `versioning/agent_traces.save_agent_answer` stores the
  trace, then the assistant message, with every passage `search_documents` showed as
  its `message_sources` (invariant 5). A source's rank is its passage number, so "[3]"
  resolves to rank 3, as it does in a RAG answer. A failed model run keeps its trace but
  gets no assistant message.
- **The citation status comes from the trace.** For agent messages, the session view
  uses the stored answer check. The RAG rule (no `[n]` = missing) would flag every
  database-only answer.
- **One question, one run.** The agent doesn't see the session's earlier turns. Each
  question is a bounded, separately traced run, as in the RAG chat ("each question is
  answered on its own"). Multi-turn memory would need a token budget per session, so it
  is left for later.
- **Both surfaces.** The console chat has a Documents/Agent switch. The portal's
  Luna defaults to agent mode ("Documents + business data"), with step chips, charts
  and suggestions in ES/EN. Both remember the choice in the browser. The inspector is
  a console page at `/agent/:traceId`; the portal opens it in a new tab.
- **The inspector.** It shows the run's totals (model calls, tool calls, prompt and
  completion tokens, time), then a timeline with one row per step and a bar for when it ran.
  Opening a model call shows its tokens, latency, text and the tool calls it requested.
  Opening a tool call shows its arguments, the result as a table of rows (or passages,
  or JSON) with a toggle to the exact text the model saw, the audited external call,
  and the chunk and score behind each passage number. Below come the answer with its
  citations and charts, and the answer check: citation status, each number and the tool
  it came from, and chart values not found in any result.
- **Charts.** recharts (new dependency, approved), loaded lazily, so it isn't in the
  main bundle (118 KB gzip, in its own chunk). The colors are the validated reference
  categorical palette in fixed order. Three of its colors are under 3:1 contrast on white,
  so every chart has a "Show the data" table, and charts with two or more series have a
  legend.

## Acceptance runs (2026-11-16)

`qwen3:4b-instruct-2507-q4_K_M` on the 7.4 GB laptop, portal channel, events timed on
arrival:

1. "Do CH-2004's Q3 2026 sales match what the Q3 meeting notes say? Also show me a
   chart of CH-2004's units sold per month in Q3." The steps streamed live:
   `sales_report` at 133 s and `search_documents` at 139 s, before the answer at 370 s. The
   answer was right (4,120 units in both sources, notes cited [1]), but Luna wrote a
   chart as JSON with made-up monthly values instead of calling `make_chart`
   (`sales_report` can't filter by SKU). The answer check missed it: it read
   `[1200, 1500, 1420]` as a citation. **Fixed:** in the agent's check, a bracket group is
   a citation only if its numbers are shown passages or have at most two digits. Any other
   bracket group is data, and its numbers are checked. A prompt rule now says charts are
   drawn only with `make_chart`.
2. The same question with a chart the tools can answer (Q3 revenue by month) ran
   twice. Both times, both tools ran and the second model call hit the 300 s Ollama
   timeout. With bge-m3 and qwen3:4b both loaded, about 1.2–2 GB is free and the model
   swaps. The error path worked: an `error` event with the `trace_id`, the trace kept,
   no assistant message. One earlier attempt answered after 310 s, but again with the
   chart as JSON and the notes uncited. The check flagged that as `missing`.
3. A follow-up in the first conversation, "Show me a chart of total revenue in MXN per
   month for Q3 2026": `sales_report`, then `make_chart` (bar, 3 values, all from tool
   results), then the answer. 3 model calls, 7,575 tokens, 106 s.

So the "done when" is met across one conversation, not in one answer: live steps and a
cited hybrid answer (run 1), then a rendered chart (run 3). A three-part question is too
much for a 4b model on this laptop. Phase 7 measures it (SKU filter for
`sales_report`, chart-as-text rate) on a machine with room for both models.

## Consequences

- The portal's footer still says Luna answers only from documents. Phase 8 rewrites
  the portal copy along with the README.
- There's no list of agent runs yet. Runs are reached from their answers. Phase 7's
  eval page can add one.
