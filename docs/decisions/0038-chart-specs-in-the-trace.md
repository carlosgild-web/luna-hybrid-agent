# 0038: Chart specs live in the trace

**Status:** accepted (2026-11-16)

## Context

PLAN_HYBRID.md Phase 5 adds `make_chart(kind, title, series)`: "backend stores the
spec; the React chat renders it (recharts)", plus an API endpoint to fetch a spec.
Two things were left open: where the spec is stored, and how a small model passes a
series when the registry only accepts flat arguments (decision 0034).

## Decision

- **Stored in the trace step, no new table.** The handler returns the spec in the
  step's `meta["chart"]`. The trace is saved anyway, so the chart is part of the run's
  record, and the run can be rebuilt from it (invariant 11). It needs no migration,
  and the handler doesn't write to the database. `GET
  /api/agent/traces/{trace_id}/charts/{chart_id}` reads the spec back from the stored
  steps, and returns 404 for an unknown trace or chart.
- **Ids per run.** Charts are numbered like passages (`ToolContext.charts`):
  `chart-1`, `chart-2`. The same spec made twice keeps its id.
- **Flat arguments.** `make_chart(kind, title, labels, series, y_label?)`.
  `series` is an object that maps each series name to its values, one per label
  (`{"Revenue MXN": [...]}`). Its JSON schema is `additionalProperties: array of
  number`, with no nested model. The plan's single `series` argument became `labels`
  plus `series`. A list or an object sent as a JSON string, and numeric labels (years),
  are accepted. A wrong shape (values that don't match the labels, a pie with two
  series or negative slices) is an error the model can fix.
- **A recharts-shaped spec.** `{"id", "kind", "title", "y_label", "series": [names],
  "data": [{"label": ..., "<series>": value}, ...]}`, one row per label. The model
  only gets back the id, the number of points and a note: the chart is shown under the
  answer, so it shouldn't repeat every value.
- **Chart values are checked.** The model copies a chart's values from a tool result,
  so they could be wrong. The answer check (decision 0036) traces each value to a tool
  result shown before the chart, with the same rounding rules as the answer's numbers.
  It lists the values it can't trace under `checks.charts`. A chart step is never
  evidence for the answer's numbers.
- **When to draw.** The tool description and a prompt rule say: only when the user
  asks for a chart or a graph, after the tool that has the numbers.

## Acceptance run (2026-11-16)

"Show me a chart of monthly revenue in MXN for Q3 2026" on
`qwen3:4b-instruct-2507-q4_K_M`: `sales_report` (Q3, by month), then `make_chart`
(bar, labels 2026-07 to 2026-09, `{"Revenue MXN": [225261.38, 132058.72, 162405.41]}`,
`y_label` MXN), then the answer "Here is a bar chart showing the monthly revenue in
MXN for Q3 2026." Check: chart-1 has 3 values, all from tool results. Took 3 model
calls, 6,625 tokens, 75 s.

## Consequences

- A chart can be shown only for a saved run. Phase 6 streams the step, then fetches
  the spec by the trace id the run ends with.
- The model types the values. A chart built straight from a step's result
  ("chart step 3, column revenue_mxn") would remove that risk, but small models fill a
  flat list more reliably than a reference. The check makes any mistake visible.
