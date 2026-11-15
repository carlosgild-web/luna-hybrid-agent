# 0032: No agent framework (no LangChain / LlamaIndex)

**Status:** accepted (2026-11-15)

## Context

This repository turns the finished *Local RAG with Ollama* app into a hybrid agent
([PLAN_HYBRID.md](../../PLAN_HYBRID.md)). Per question, Luna decides whether to search
the documents, query the business database, call an external API, or combine them.
Agent and RAG frameworks (LangChain, LangGraph, LlamaIndex) are the usual way to build
that, so not using one needs a reason on record.

Functionality comes first. The frameworks add no capability this project needs, and
they would make the parts worth showing harder to work on.

## Decision

> **Why no LangChain / LlamaIndex**
>
> This project deliberately uses no agent or RAG framework. The reasons are practical,
> not ideological:
>
> 1. **The core loop is small.** Ollama exposes native tool calling; the agent loop
>    (send tools → execute `tool_calls` → feed results back → stop when there are none)
>    is ~150 lines and fully readable. A framework would hide exactly the part a
>    reviewer wants to see.
> 2. **The retrieval pipeline already exists and is measured.** Hybrid search, RRF
>    fusion, reranking and citation repair were built and evaluated in the first
>    project (0.956 MRR). LlamaIndex would replace working, measured code with an
>    abstraction over the same ideas.
> 3. **Traceability is the product.** Every step — model request, tool, arguments, rows
>    returned, latency — is stored and shown in the agent inspector. Owning the loop
>    makes that trivial and framework-agnostic.
> 4. **Small local models need visibility.** With a 3B–7B model, debugging tool calls
>    means reading the exact JSON that goes to and from Ollama. Fewer layers, faster
>    fixes.
> 5. **Dependency surface.** The first project runs offline on a 7 GB laptop. Three
>    fast-moving packages with frequent breaking API changes would raise maintenance
>    cost without adding a capability the project needs.
>
> I know LangGraph and LlamaIndex and when they pay off: multi-agent orchestration,
> persistent checkpoints, human-in-the-loop, or many data connectors. None of those
> are requirements here.

### Evaluation summary

| Criterion | No framework (this plan) | LangGraph | LlamaIndex |
|---|---|---|---|
| New code for the agent loop | ~150–200 own lines | ~100 lines + 3 dependencies | n/a (RAG only) |
| Risk to what already works | None: retrieval is only wrapped | Low, but `LLMClient`/fakes must adapt to its interfaces | High: duplicates the RAG pipeline |
| CI fakes without Ollama | Already exist | Must learn/adapt theirs | — |
| Agent inspector | Designed here, full control | Possible via stream events, one more layer | — |
| API stability | Depends only on Ollama's `tools` API | Frequent breaking changes; pin versions | Same |
| Debugging with local qwen2.5 | Exact JSON in/out visible | Abstractions in between | — |

## Consequences

- The agent lives in one module, `backend/app/agent/` (loop, registry, tools, trace),
  built on the existing `LLMClient` interface, which gains `chat_with_tools()`.
- The CI fakes keep working: a fake client scripts `tool_calls` for the loop tests.
- Things a framework would give for free, such as retries on malformed tool arguments
  and the iteration limit, are written and tested here (PLAN_HYBRID.md, Phase 3).
- Revisit if the project ever needs multi-agent orchestration, persistent checkpoints
  or human-in-the-loop approval of tool calls.
