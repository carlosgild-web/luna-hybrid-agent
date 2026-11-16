# 0036: Documents as a tool, and checking the answer against its trace

**Status:** accepted (2026-11-16)

## Context

Phase 4 of PLAN_HYBRID.md wraps the RAG pipeline as the agent's `search_documents`
tool and extends the citation rules: "document claims cite passages, numeric claims
name their tool". Decision 0035 left one more job for this phase: a check that
catches numbers the model made up (invariant 12).

## Decision

- **One retrieval path.** `search_documents(query, shelf?)` runs the employee portal's
  retrieval (`prepare_context_many`: glossary, vectors + FTS5 fused with RRF, the
  reranker when it's on) over the shelves visible in the portal (decision 0010). The
  agent and the portal find the same passages for the same words.
- **The tool gets a read-only library, not the database.** `retrieval/library.py`
  holds `DocumentLibrary`, which only selects. The handler in `agent/tools/documents.py`
  calls `ctx.documents.search(...)`, so the import guard of invariant 9 (no
  `app.db`, `app.versioning`, `app.ingest` or `app.jobs` in tool modules) still holds.
- **Passage numbers are run-wide.** `ToolContext.citations` numbers chunks for the
  whole run, and `run_agent` gives every run a fresh book (`ToolContext.for_new_run()`).
  "[3]" names one passage even when the model searches twice, and a chunk found again
  keeps its number. The model sees `{"cite": "[1]", "document", "where", "text"}`.
- **Provenance the model doesn't see.** A handler may return a `ToolOutput` with `meta`.
  The trace stores it on the tool step next to the verbatim result (invariant 11 is
  unchanged). For `search_documents` it holds the chunk, document, version and score
  behind each number. Phase 6 needs them for the message sources (invariant 5).
- **Lenient shelves.** The tool's description lists the real shelf names. A name that
  matches no shelf, even after case, accents, `_`/`-`/spaces and a unique prefix are
  taken into account, searches every shelf and says so. It is not an error, because a
  small model can't fix a name it had to guess. With no visible shelf, the tool isn't
  offered, and the citation rules stay out of the system prompt.
- **Hybrid prompt.** With the tool offered, the system prompt says which source
  answers what: documents for what was written or decided, the database for stock,
  sales and purchases, and both for a comparison. It also says to cite document facts
  as [n], to attribute database facts to the database in words, and to report
  disagreements with both figures.

### Deviation: the check names the tools, not the answer

The plan reads as if the answer text should name the tool behind each number. Instead,
the model is asked to say in words that a figure comes from the database ("the sales
records show…"), and `agent/answer.py` does the exact attribution after the run:

- **Numbers.** Every number in the answer is traced to what contains it: a tool
  (`sales_report`), one passage (`search_documents [1]`), the question, or today's
  date. A number nothing contains is listed in `unsupported_numbers`. Readings cover
  "4,120", "4.120", "519.725,51" and "59,8 %". A rounded figure matches at the
  precision it was written ("519,726" matches 519,725.51; "520,000" doesn't). SKUs,
  quarters, list markers and citation markers are not numbers.
- **Citations.** The statuses are the RAG app's: `ok`, `missing`, `invalid` (a
  number no search showed), `not_applicable`. The cited passages' metadata goes with
  them.

Tool names in prose cost a 4B model attention it needs for the facts (decision 0007
saw a longer citation rule break answers), and nobody would verify them. The check is
deterministic, it runs on the trace alone (so it can be rerun on any stored run), and
the agent inspector of Phase 6 can show it per number. Each run stores it in
`agent_traces.checks` (migration 0012). The CLI prints it under the answer.

The check reports and changes nothing. Known limits:

- A count matches any list of that length or any equal value. "1 unit" passes when a
  tool returned one row, which was the `granite4:micro` mistake in decision 0035.
- A correct number the model computed itself (a difference, a sum) is reported as
  unsupported. Invariant 12 says that's the right call.
- Citation repair (decision 0020) is not applied to agent answers. Its rule "a
  sentence that cites nothing gets the passage that shares its number" would attach
  a document citation to a database sentence that happens to share a figure with
  that document, which is exactly the hybrid case.

## The acceptance run

"Do CH-2004's Q3 sales match what the Q3 meeting notes say?" on
`qwen3:4b-instruct-2507-q4_K_M`, with the 11 demo documents and the seeded business
database:

- First model call (1,549 + 90 tokens, 38 s): `sales_report` (Q3 2026, by SKU) and
  `search_documents` ("Q3 sales meeting CH-2004 units", shelf `meetings`) together.
  Both tools returned the right data: 4,120 units, and the notes as passage [1].
- Second call: past the 300 s HTTP timeout. While the laptop was running WSL (3 GB),
  SQL Server and the Power BI gateway, with 0.8 GB free, loading `bge-m3` for the
  query pushed the chat model's weights out of RAM.
- The same second call, replayed from the stored trace with only the chat model
  loaded (3,261 + 99 tokens, 277 s, still swapping): "Yes, CH-2004's Q3 sales match
  what the Q3 meeting notes say. The sales report shows that CH-2004 ('Crescent Moon
  & Star') had 4,120 units sold in Q3 2026 [sales_report]. The meeting notes state
  that CH-2004 was the best-selling SKU with 4,120 units sold [1]."

<!-- ACCEPTANCE-END -->

## Consequences

- Every answered run carries `checks`, so Phase 7's evaluation can count unsupported
  numbers and citation failures straight from the traces.
- A hybrid run needs the chat model and the embedding model in memory together. On
  8 GB that only fits with little else running (see the acceptance run).
