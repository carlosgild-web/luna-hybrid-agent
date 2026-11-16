# 0034: Tool registry with pydantic arguments; business tools in English over a Spanish DB

**Status:** accepted (2026-11-16)

## Context

Phase 2 of PLAN_HYBRID.md adds `agent/registry.py` and `agent/tools/business.py` with
`check_inventory`, `sales_report`, `supplier_purchases` and `sql_select`, as plain
functions with no LLM yet. Section 4 of the plan lists their parameters. Writing them
raised a few questions the plan doesn't answer, and two places where it changes.

## Decision

- **A tool's arguments are a flat pydantic model.** The registry builds Ollama's JSON
  schema from it and validates each call before the handler runs. Pydantic is already
  installed with FastAPI, so this adds no dependency. The schema is trimmed for small
  models: `Optional[X]` is just `X`, left out of `required`, and the `title` fields are
  removed. Nested models are refused.
- **Mistakes go back to the model, bugs don't.** `execute` returns `ok=False` with a
  readable error for an unknown tool, arguments that aren't a JSON object, a field that
  fails validation (the error names the field), or a `ToolError` from the handler: an
  unknown SKU, an unknown supplier, an SQL error, a missing database. The model can fix
  these and call again (the loop's retry-once comes in Phase 3). Any other exception is a
  bug and propagates.
- **Lenient where it's safe.** Unknown keys are ignored; `null` and `""` count as left
  out; pydantic coerces `"5"` to 5 and `"true"` to true. Small models do all of these.
- **English for the model, Spanish in the database.** The typed tools return English
  keys and translate coded values (channel `Mayoreo` becomes `wholesale`, category
  `Dije` becomes `charm`, status `En tránsito` becomes `in transit`). Names of products,
  customers and suppliers stay as they are. `sql_select` returns raw rows, and its
  description carries the Spanish schema with the coded values. A test checks that
  schema against the real tables.
- **MXN per sales line is its share of the order's stored MXN total.** `pedido_detalle`
  has no MXN column. Multiplying by the rate on every line misses the books by a cent per
  quarter, while a share of `pedidos.total_mxn` adds up exactly in every grouping (Q3:
  519,725.51 MXN). Shares of revenue are computed in the tool, so the model never does
  the arithmetic (invariant 12).
- **`sales_report` gets `order_by` (revenue or units), which the plan doesn't have.**
  "Top 5 SKUs by units in Q3" needs a ranking by units, and a small model sorts 12 rows
  badly. Default: months and quarters in date order, anything else by revenue.
- **`supplier_purchases.status` is `received`, `received_late` or `in_transit`.**
  `received` includes the late ones. `supplier` also matches the contract number
  (XA-2026-014), ignoring case and accents.
- **A date range with no orders says what the data covers** instead of returning bare
  zeros, so "Q1 2027 revenue" gets "unknown", not "0".
- **`sql_select` has four layers.** The text check (`tools/sql_guard.py`) blanks out
  string literals, quoted identifiers and comments, then requires a single statement
  that starts with SELECT or WITH and contains no writing keyword. Under it is the
  read-only connection from decision 0033 (three more layers). Each run is also capped
  at 200 rows, 3 seconds (a progress handler stops runaway recursive CTEs) and 500
  characters per cell.

## Consequences

- Phase 3 calls `registry.ollama_tools()` and `registry.execute()`, and stores each
  `ToolResult.to_message()` verbatim in the trace (invariant 11).
- A new tool is a pydantic model, a function and a `Tool(...)` entry. `build_registry()`
  in `agent/tools/__init__.py` is where documents, FX and charts get added later.
