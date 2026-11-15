# Luna Hybrid Agent

[![CI](https://github.com/carlosgild-web/luna-hybrid-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/carlosgild-web/luna-hybrid-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

*Python · FastAPI · SQLite · ChromaDB · Ollama (native tool calling) · React + TypeScript · Docker*

> **Status: work in progress (Phase 0 of 8).** This repo starts from the finished
> [Local RAG with Ollama](https://github.com/carlosgild-web/local-rag-ollama) app
> (v1.0.1), imported as one baseline commit. The agent is being built on top of it,
> one phase at a time. Right now the app works exactly like v1.0.1. The roadmap is in
> [PLAN_HYBRID.md](PLAN_HYBRID.md).

## What this will be

**Luna**, the assistant of the fictional bead maker *Hilda & Luna Beadworks*, becomes a
hybrid agent. She no longer only searches documents. For each question she decides
which tools to use, and she shows every step:

- **Documents (RAG):** the existing hybrid search (vectors + keywords, RRF, reranker),
  wrapped as a tool, with numbered citations as before.
- **Business database:** inventory, sales and purchasing in a seeded SQLite database,
  opened read-only. Typed tools cover the common questions, and a guarded `sql_select`
  (one SELECT, row cap) covers the rest.
- **One external API:** the MXN/USD exchange rate. It is off by default; without it, Luna
  uses the stored rate and says so.
- **Charts:** the agent can return a chart that the chat renders.

A question such as *"Do CH-2004's Q3 sales match what the Q3 meeting notes say?"* needs
both a SQL aggregate and a meeting-notes passage. The answer cites the passage and names
the tool behind each number. The **agent inspector** shows the full run: each model
request, tool call, its arguments, the rows returned, tokens and timings.

Everything runs locally on Ollama (`qwen2.5`, which supports tool calling). The agent
loop is bounded, and every run is stored as a trace that the answer can be rebuilt from.
A tiered gold set of about 30 questions (documents only, database only, external,
hybrid, and refusals) measures tool selection, SQL correctness and groundedness, with
fakes in CI.

## Why no LangChain / LlamaIndex

The agent loop is small (send tools, run the `tool_calls`, feed the results back, stop
when there are none), and owning it keeps every step visible. That matters when you
debug a 3B–7B local model's tool calls. The retrieval pipeline already exists and is
measured, and the app runs offline on a 7 GB laptop with few dependencies. The full
reasoning and the comparison table are in
[decision 0032](docs/decisions/0032-no-agent-framework.md).

## Run the current baseline

Until the agent phases land, this runs the v1.0.1 app: document library, versions,
cited chat, the demo company's website and intranet.

```bash
git clone https://github.com/carlosgild-web/luna-hybrid-agent.git
cd luna-hybrid-agent
.\start.cmd      # Windows (or double-click it)
./start.sh       # macOS / Linux
```

See [GETTING_STARTED.md](GETTING_STARTED.md) for the details. The
[v1 README](https://github.com/carlosgild-web/local-rag-ollama#readme) describes every
feature of the baseline.

## License

[MIT](LICENSE)
