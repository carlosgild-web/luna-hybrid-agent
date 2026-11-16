# 0035: The agent loop, its limits, and what small models needed

**Status:** accepted (2026-11-16)

## Context

Phase 3 of PLAN_HYBRID.md adds the agent loop: `LLMClient.chat_with_tools()`, a trace,
limits, the `agent_traces` table and `python -m app.cli agent`. The plan was approved
before coding. This record lists how the limits work and where the code differs
from the plan, mostly because of what real small models did in the acceptance run.

## Decision

- **One loop, no framework** (decision 0032). `agent/loop.py` calls
  `chat_with_tools` (non-streaming `/api/chat` with `tools`), runs the requested calls
  through the registry, sends the results back as `tool` messages and stops when the
  model answers in text. `LLMClient.chat_with_tools` has a default that raises
  `NotImplementedError`, so only Ollama and the fake implement it.
- **Limits.** `AGENT_MAX_ITERATIONS` (6) counts model calls, the final one included.
  `AGENT_TOKEN_BUDGET` (32,000) counts Ollama's prompt and completion tokens over the
  whole run. It is checked before each call, so the final call can go over by one
  call's worth. Each tool runs in a daemon thread for at most `AGENT_TOOL_TIMEOUT_S`
  (20 s). Python can't stop a thread, so a tool that overruns finishes on its own and
  its result is dropped. When a limit is reached, the tools are no longer offered and
  one last call answers from what the run has.
- **Mistakes the model can fix go back to it.** An unknown tool, bad arguments or a
  `ToolError` come back as an error the model can read, and it may retry. The same
  tool failing **twice in a row** stops the tools with a new status, `tool_errors`. The
  plan listed four statuses (`answered`, `max_iterations`, `token_budget`,
  `llm_error`), and this run ends for none of those reasons.
- **A handler bug doesn't lose the trace.** Decision 0034 lets a bug propagate out of
  `registry.execute`. The loop catches it, logs the traceback, and shows the model only
  "internal error (RuntimeError)". Every run then still writes its trace (invariant 8).
- **The same call twice reuses the first result.** The key is the tool name plus the
  arguments with sorted keys. Failed results are reused too, and the repeat counts as
  a second failure.
- **Tool calls written as text.** qwen2.5 sometimes writes a call as JSON in its reply
  instead of using the tools API. When the whole reply is a call to a known tool (bare
  JSON, a list, a ```json fence, `<tool_call>` tags, or the `{"function": ...}`
  shape), it is run and the step is marked `parsed_from_content`. Anything else is an
  answer.
- **New: an empty reply is retried once.** With Ollama 0.40.16 and `qwen2.5:3b`,
  `/api/chat` returned neither text nor tool calls for the acceptance question, after
  generating 24 tokens. The same prompt through `/api/generate` with `raw` gave a valid
  `<tool_call>`, so Ollama's parser drops a call it can't read. The same prompt fails
  every time at temperature 0. Adding a short user message ("Your last reply was
  empty…") without the empty assistant turn makes the next call parse. This happens
  once per run, counts as an iteration, and the empty step stays in the trace.
- **The final instruction is a user message.** qwen2.5's chat template renders one
  system message, at the top. A second system message telling the model to stop
  calling tools would not show up where it is needed.
- **Storage.** Migration 0011 creates `agent_traces` with prompt and completion tokens
  in separate columns. `versioning/agent_traces.py` saves a trace and one `agent_run`
  audit row (status, model calls, tools used) in the same transaction. The loop stores
  nothing itself. The link from a chat message to its trace comes with the chat
  (Phase 6).

## The acceptance run, and the model

"¿Qué productos necesitan resurtido?" had to call `check_inventory` and name
GB-1014, CH-2004 and BN-9001. The plan recommends `qwen2.5:7b`. The development
laptop has 7.2 GB of RAM:

| Model | Result |
|---|---|
| `llama3.1` 8B (6.3 GB loaded) | Right call (`only_below_reorder`) in 240 s. The second call ran past the 300 s HTTP timeout while swapping. |
| `qwen3:4b` | Right call after 417 thinking tokens (126 s). The second call's thinking ran past 300 s. |
| `qwen2.5:3b` | Empty reply, retried. Then the right call and a correct answer with the tool's numbers: 3 model calls, 3,838 tokens, 42 s. |

### Choosing a model for 8 GB

The 3B model passed, so two more small tool-calling models from Hugging Face were
compared with it. Each got three questions: the restock question, "¿Cuánto vendimos en
el tercer trimestre de 2026 y qué porcentaje fue mayoreo?" (expected 519,725.51 MXN,
59.8% wholesale) and "How many units of CH-2004 do we have in stock?" (1,900). They ran
on the same laptop, at temperature 0, one model loaded at a time.

| | `qwen2.5:3b` | `qwen3:4b-instruct-2507-q4_K_M` | `granite4:micro` |
|---|---|---|---|
| RAM when loaded | 2.4 GB | 3.9 GB | 2.9 GB |
| Restock | right | right | right |
| Q3 sales, wholesale share | wrong: grouped by quarter, then said "100% wholesale" | right: `group_by: channel`, 519,725.51 MXN, 59.8% | wrong: grouped by quarter, then said "100% of the year" |
| CH-2004 stock (English) | right | right | wrong: "1 unit" |
| Calls dropped by Ollama | 1 (rescued by the retry) | 0 | 0 |
| Total time | 93 s | 128 s | 106 s |

All three models chose the right tool every time. The two wrong sales answers made up
a number the tool result didn't contain, which breaks invariant 12. `granite4:micro`
also writes SKUs with a non-breaking hyphen (U+2011), so they no longer match exactly.

**The phase is accepted on `qwen3:4b-instruct-2507-q4_K_M`**. It is the non-thinking
2507 release of Qwen3-4B, Apache 2.0, with BFCL-v3 61.9 on its model card against 57.6
for Qwen3-4B without thinking. On the restock question it called `check_inventory` on
the first try and named the three SKUs correctly: 2 model calls, 2,874 tokens, 94 s
with the model loaded cold.

The model recommendation is now:

- **8 GB of RAM:** `qwen3:4b-instruct-2507-q4_K_M`. It is about 40% slower than
  `qwen2.5:3b`, but it was the only one of the three that kept its numbers to the
  tool results.
- **16 GB:** `qwen2.5:7b` stays the recommendation, as in the plan.

`AGENT_MODEL` picks the model, and its default is still `CHAT_MODEL`. One run per question
shows a trend but isn't an evaluation. Phase 4's numeric-claim check is what catches
made-up figures.

## Consequences

- An agent answer can be rebuilt from its row in `agent_traces`. Each tool step stores
  the exact string the model was shown (invariant 11).
- The empty-reply retry works around an Ollama bug, seen only with `qwen2.5:3b` so far.
  It stays as a safety net. If a later Ollama parses these calls, the retry stops
  triggering and nothing else changes.
- Streaming the steps over SSE and checking numeric claims come later (Phases 4 and 6).
