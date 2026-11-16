# 0037: Local-first, external by explicit consent (the FX tool)

**Status:** accepted (2026-11-16)

## Context

Everything in this app runs on the user's machine. The one exception PLAN_HYBRID.md
allows is the USD/MXN exchange rate (Phase 5), because a question like "convert Q3
revenue to dollars at today's rate" needs a number the company's data doesn't have.
Invariant 10 sets the terms: no external call unless `ALLOW_EXTERNAL_APIS=true`,
every external call audited, and a failure that degrades to the stored value instead
of an error answer. This record says how `fx_rate` meets them.

## Decision

- **Off by default, and still useful when off.** With `ALLOW_EXTERNAL_APIS=false`, no
  request leaves the machine. `fx_rate` answers with the rate stored in the business
  database: the latest order's `tipo_cambio`, which is 18.50 for every order. The
  result says `"source": "stored"` and includes a note telling the model to say this
  is the books' rate, not today's market rate. The system prompt repeats the rule. So
  the tool is always offered, and the answer is honest either way.
- **frankfurter.dev, not Banxico.** The v2 API (`/rate/USD/MXN[?date=YYYY-MM-DD]`)
  needs no key, so a clone runs without signing up for anything. It publishes central
  bank reference rates, which is enough for a demo. `FX_API_URL` points elsewhere for
  a mirror or a self-hosted copy.
- **Failure degrades, never errors.** A timeout (`FX_TIMEOUT_S`, 5 s, well inside the
  agent's 20-second tool limit), a connection error, a non-200 reply or a body it
  can't read all give the stored rate. The note names the reason ("HTTP 503"). A
  future date or a negative amount is still an error, because the model can fix those.
- **Cached in the process.** One `FxRates` per process (`shared_rates(settings)`)
  keeps answers: today's rate for `FX_CACHE_TTL_S` (1 hour), a past day's for good,
  since it won't change. Failures aren't cached. The cache lives only in memory, and a
  restart asks again.
- **The tool converts.** `fx_rate(date?, amount?, currency?)` adds `amount` and
  `currency` to the plan's `date?`. With them, the tool returns the converted amount,
  rounded to cents, and both directions of the rate (`rate`, `usd_per_mxn`). Without
  this, the model would multiply, and the answer check would flag its result as
  unsupported (invariant 12).
- **Audited through the trace, not by the handler.** A tool handler doesn't write to
  the database (invariant 9's spirit, and the import guard). It records each request
  it actually sent in the step's `meta["external_call"]`: URL, ok or the error, the
  rate, and the time. `save_trace` writes one `external_call` audit row per such step,
  in the same transaction as the trace. A cache hit sends nothing and writes no row.
  A step the loop reused from an earlier identical call writes no row either.

## Acceptance run (2026-11-16)

"How many Mexican pesos is 250 US dollars at today's exchange rate?", asked through
`python -m app.cli agent` on `qwen3:4b-instruct-2507-q4_K_M` with the seeded
business database:

- **Flag off.** One call: `fx_rate {"amount": 250, "currency": "USD"}`. It returned
  `source: stored`, rate 18.5, converted 4,625.0, and the stored-rate note. Answer:
  "4625.0 Mexican pesos. Note: This rate is stored in the company's books, not today's
  market rate." The number was traced to `fx_rate`. No request was sent, and the only
  audit row is `agent_run`. Took 2 model calls in 98 s; the first call loaded the model.
- **Flag on.** The same tool call returned `source: live`, rate 18.5686 for
  2026-11-16, and converted 4,642.15. Answer: "4642.15 Mexican pesos." The number was
  traced to `fx_rate`. Audit: `external_call`, "fx_rate: GET
  https://api.frankfurter.dev/v2/rate/USD/MXN ok, rate 18.5686 (576 ms)". Took 2 model
  calls in 20 s.
- **Cache.** In one process, a second `fx_rate` call with another amount reused the
  live rate without a request and without an `external_call`.

## Consequences

- An external call is audited when its run is saved. Every caller saves its trace
  (invariant 8), so this covers the CLI now and the chat in Phase 6. If the loop's
  own tool timeout dropped an FX result, its meta would be lost with it. The FX
  timeout is a quarter of the tool limit, so that doesn't happen in practice.
- The stored rate is a single fixed number. The note says so when a day is asked for
  ("The books use this one rate for every order"), so the model doesn't present
  18.50 as that day's market rate.
- Turning the flag on is the user's choice, made in `.env`. Nothing in the UI flips it.
