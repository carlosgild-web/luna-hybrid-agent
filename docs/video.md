# The video

A 2½-minute recording of the real app with the real local model, `qwen3:4b-instruct-2507`
on Ollama, on a 7.2 GB laptop with no GPU (decisions 0042 and 0043). On the left is the
employee portal and then the admin console. On the right, an "Under the hood" panel shows
the code behind each step and the data that passed through it.

## What it shows

| Time | Step | What to notice |
|---|---|---|
| 0:00 | Title | |
| 0:06 | "Which products need restocking?" | The model gets the question and the tool schemas and asks for `check_inventory` with `only_below_reorder: true`. The registry checks the arguments against the tool's schema before the tool runs. |
| 0:22 | The answer | Three products, each number traced by the answer check to the `check_inventory` result. The panel then shows the rows exactly as the model saw them, from the read-only business database. |
| 0:33 | "Do CH-2004's Q3 sales match what the Q3 meeting notes say?" | One model call asks for both tools: `search_documents` (hybrid search: bge-m3 vectors and SQLite FTS5, fused with RRF) and `sales_report` (an aggregate in SQL). |
| 0:52 | The answer check | 4,120 is in the passage [1] and in the `sales_report` result. The model also cited the sales report as "[2]", which is not a passage: the check marks the citations `invalid`. |
| 1:00 | Why that passage | The vector rank, keyword rank and fused score of the passage, from the retrieval inspector (`POST /api/retrieve/inspect`, read only), for the run's own query and shelf. |
| 1:07 | `[1]` | The citation opens the passage; the line with 4,120 is highlighted. |
| 1:10 | "Show me a bar chart of Q3 2026 revenue by month." | The model asks `sales_report` for the months, then passes those numbers to `make_chart`, which stores a chart spec in the trace. **Show the data** is the same spec as a table. |
| 1:41 | "¿Cuántos dólares son 10,000 pesos?" | `fx_rate`, the one tool that could call an external API. External calls are off by default, so it answers with the stored rate (18.5) and says so: 540.54 USD. |
| 1:58 | "Delete the orders table." | The model refuses: no tool can write. The panel shows the SQL guard's verdict on `DELETE FROM pedidos` (`video/introspect.py`, the backend's own `check_select`) and the read-only connection. |
| 2:16 | The agent inspector | The hybrid run rebuilt from its stored trace: two model calls (26 s and 194 s), the two tools, 6,372 prompt tokens, and the answer check, which reports the invalid citation. The `sales_report` step opens on the twelve rows the model saw. |
| 2:28 | End | |

## Is it real?

- **The model is real; its run is replayed.** The video was recorded in two passes
  (decision 0043), because on this laptop qwen3 and a browser recording 1080p video don't
  fit in memory together. In pass 1 the five questions ran on qwen3 through the app's own
  chat API with no browser. In pass 2 the same app, on fresh data, replayed qwen3's outputs
  word for word, each after waiting as long as the real call took, while the browser
  recorded. The banner on every frame says so.
- **Everything else ran live in the recording:** the agent loop, the tool registry, the
  tools, the bge-m3 search, the read-only business database, the SQL guard, the answer check,
  the stored trace and the inspector. `cues.mjs` checked that every model step, tool call,
  tool result and answer of the recording equals pass 1 (five of five runs).
- **Every wait is labelled.** Model waits over 3 s are cut to 1 s with their real length:
  99, 40, 25, 5, 184, 16, 35, 17, 9, 20 and 12 s (about 8 minutes of model time in all).
- **The panel shows captured data.** Each payload is the SSE event, the stored trace, the
  retrieval inspector's answer or the guard's verdict from the take. `cues.mjs` compares each
  one with the capture again (16 of 16), checks that the ranking shown is the order the run's
  search returned, and that no local path appears. Code excerpts are found by file and
  symbol (`lines.mjs`), so their line numbers are the current ones.
- **One highlight is added:** the line with 4,120 in the cited passage is marked for the
  reader; its text is unchanged.

Known in this take: the model cited the sales report as "[2]" (the check caught it, as
above); in the chart question it called `sales_report` first, which gets a generic row; and
its refusal explains that deleting tables is outside its tools.

## Record it again

See [video/README.md](../video/README.md). In short, on the development laptop:

```bash
start.cmd video                                                   # pass 1
backend/.venv/Scripts/python video/capture.py --out video/out/pass1
start.cmd video stop
# pass 2: start.cmd video again (fresh data), stop it, start video/replay_server.py
# with the same settings, then:
cd video && npm ci && VIDEO_REPLAY_DIR=video/out/pass1 npm run video:ollama
```

With 16 GB or more, one live pass works: `start.cmd video`, then `npm run video:ollama`. The output in
`video/out/ollama/` is `video.mp4`, `video-readme.mp4` (1280x720, under 10 MB, for the
README), `poster.png` and `subtitles.en.srt`. `npm run video:demo-dry` rehearses the same
steps on the evaluation suite's scripted model.
