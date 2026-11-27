# The README video

Records "a real model, under the hood" from the running app (decision 0043, tooling ported
from hl-bead-studio). [docs/video.md](../docs/video.md) says what it shows. Recording needs
Windows (Edge), ffmpeg on the `PATH`, Ollama with `qwen3:4b-instruct-2507-q4_K_M` and
`bge-m3`, and the backend venv.

```bash
cd video && npm ci          # @playwright/test only; uses the installed Edge
npm run video:check         # every [file, symbol] in storyboard-ollama.json still resolves
npm run video:test          # how compose.mjs cuts a take
npm run video:smoke         # ~10 s: the 1920x1080 stage -> out/smoke/ (any running app)
```

## Record it

1. Quit Ollama, set `OLLAMA_MAX_LOADED_MODELS=1`, start it again, and close memory-heavy apps.
2. `start.cmd video`: a fresh `data-video\` with the demo company, on port 8010, the model
   warmed (decision 0042). Your `data\` is not touched.
3. `npm run video:ollama` (20-40 min on a laptop CPU). It writes to `out/ollama/`:
   - `take.webm`, the stage as recorded;
   - `video.mp4`, with every model wait over 3 s shortened to 1 s and labelled with its
     real length;
   - `video-readme.mp4` (1280x720, under 10 MB), `poster.png` and `subtitles.en.srt`;
   - `take.json` (the models the runs used, the rows that never matched), `events.json`,
     `shown.json`, `trace-<turn>.json`, `inspect-hybrid.json`.

`npm run video:verify` re-checks a take: rows, every payload against the capture, the
search ranking against the take's own search, the models, no paths.

### The dry run

The same steps on the evaluation suite's scripted model, against the README GIF's harness:

```bash
cd e2e/showcase && E2E_STEP_DELAY_MS=4000 ../../backend/.venv/Scripts/python harness.py
# in another terminal
cd video && BASE_URL=http://127.0.0.1:8020 npm run video:demo-dry   # -> out/demo-dry/
```

## Files

| File | Role |
|---|---|
| `storyboard-ollama.json` | The rows: trigger, subtitle, callouts, payload source; flexible mode (a real model's steps vary) |
| `record.spec.ts` | The take: guard, capture, conductor, scenes (`@smoke`, `@demo-dry`, `@ollama`) |
| `stage.html`, `stage.js` | App iframe, "Under the hood" panel, subtitles, slides |
| `payload.mjs` | How a row's payload is cut from the capture; shared by the take and the checks |
| `lines.mjs` | `file:symbol` → current line and excerpt |
| `introspect.py` | The SQL guard's verdict from the backend's own `check_select` (read only, backend venv) |
| `cues.mjs`, `compose.mjs` | Cues, checks, wait cuts, `.mp4`, `.srt`, the README copy (ffmpeg) |
| `compose.test.mjs` | `npm run video:test` |

Where the panel's data comes from: the SSE steps (the model call's tool choice, each tool's
arguments), the `answer` event (the answer check), the run's stored trace (tool results
verbatim), and the retrieval inspector (`POST /api/retrieve/inspect`, read only) for the
vector, keyword and fused ranks of the run's own search. `cues.mjs` fails the take if that
ranking differs from the order the search returned in the run.

## Findings (phase 9, steps L4-L5, 2026-11-27)

Three dry takes on the scripted model before the first Ollama take.

| # | Finding | Kind | Fix |
|---|---|---|---|
| F1 | An answer that arrived within 2 s of the question vanished from the chat (found building the GIF) | Product | Fixed in `useChatStream.ts` (decision 0042) |
| F2 | The pipeline's nine layer names overflowed the 640 px panel | Tooling | Shorter names (API, Agent, SQLite, Check), checked by the smoke test |
| F3 | The "Demo guide" pill is a button with text, no label: a CSS rule on `aria-label` missed it | Tooling | Hidden by text, as in hl-bead-studio |
| F4 | The stream carries no tool results, and the trace exists only after `done` | Expected | Live rows show the tool choice and arguments; result rows, the RRF ranks and the guard come after the turn |
| F5 | `findGaps` never cut the stretch from the last event to `done` | Tooling | Cut too, with a test |
| F6 | The previous scene's subtitle stayed on screen through "New conversation" and the typing | Tooling | The typed row comes up as the typing starts |
| F7 | The README copy came out at 10.1 MB: two-pass x264 overshoots its target by about 6 % | Tooling | Target 8.5 MB (9.3 MB in the third dry take) |
| F8 | The chart turn calls `sales_report` first, which the chart scene has no row for | Expected | One generic row (cap `VIDEO_MAX_GENERIC=2`) |

Checked in every dry take: all 26 rows shown, 16 payloads equal the capture, the retrieval
inspector's ranking equals the run's search, no local path in any payload or in the app's
text. Length 2:23-2:34 with 5 waits cut (the harness pauses 4 s per model turn).
