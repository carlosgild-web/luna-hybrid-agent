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

## Record it (two passes, decision 0043)

On the 7.2 GB development laptop qwen3 and the recording browser don't fit together, so the
model runs first, alone, and the recording replays its outputs:

1. Restart Windows, close every other app, and set `OLLAMA_MAX_LOADED_MODELS=1` for Ollama.
2. **Pass 1:** `start.cmd video`, then
   `backend/.venv/Scripts/python video/capture.py --out video/out/pass1`: the five questions on
   qwen3 through the app's chat API, no browser. Then `start.cmd video stop`.
3. **Pass 2:** `start.cmd video` again (fresh data), `start.cmd video stop`, `ollama stop` the
   model, then start `video/replay_server.py --pass1 video/out/pass1` with the same settings
   (`DATA_DIR=data-video`, `AGENT_MODEL`, `EMBEDDING_MODEL=bge-m3`) and run
   `VIDEO_REPLAY_DIR=video/out/pass1 npm run video:ollama`. It writes to `out/ollama/`:
   - `take.webm`, the stage as recorded;
   - `video.mp4`, with every model wait over 3 s shortened to 1 s and labelled with its
     real length (pass 1's);
   - `video-readme.mp4` (1280x720, under 10 MB), `poster.png` and `subtitles.en.srt`;
   - `take.json` (the models, the rows that never matched, `replay_of`), `events.json`,
     `shown.json`, `trace-<turn>.json`, `inspect-hybrid.json`.

`npm run video:verify` re-checks a take: rows, every payload against the capture, the
search ranking against the take's own search, every step against pass 1, the models, no
paths. On a machine with 16 GB or more, one live pass works too: `start.cmd video`, then
`npm run video:ollama` without `VIDEO_REPLAY_DIR`.

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
| `capture.py`, `replay_server.py` | Pass 1 (the questions on Ollama, no browser) and pass 2 (the app replaying pass 1's model outputs) |
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
| F9 | First Ollama take (L6): the model calls took 139-153 s each, and the browser stopped painting after ~170 s while JavaScript kept running; the next click timed out. An idle page alone kept painting (probe, 160 s): the display going off (Windows' native occlusion tracking) or memory pressure (3.1 GB free) | Tooling | Edge runs with `CalculateNativeWinOcclusion` off, the take script keeps the display on, and the take checks for an animation frame before and after every turn |
| F10 | Second Ollama take: in the hybrid question qwen3 asked for both tools in one call; the next call (qwen3 reloaded after bge-m3, a long prompt, the recorder on the CPU) hit the client's fixed 300 s timeout | Product (setting) | `OLLAMA_TIMEOUT_S` (default 300), 900 in video mode; a turn that ends in an error now stops the take with that error (decision 0042) |
| F11 | Third Ollama take: the browser stopped painting the moment the first answer arrived, with the display on (3.3 GB free; the page file had peaked at 7.4 GB) | Tooling | Edge without its GPU process (`--disable-gpu`); Ollama with flash attention and an 8-bit KV cache; the take waits up to 3 min for painting to resume and logs the stall as a labelled wait, cut to 1 s like a model wait |
| F12 | Fourth Ollama take: with `OLLAMA_TIMEOUT_S=900` the hybrid call ran past 900 s; flash attention and the 8-bit KV cache made qwen3 ~45 % slower on this CPU | Method | Two passes (decision 0043): the model runs alone (`capture.py`), then the app replays its outputs while the browser records (`replay_server.py`); `cues.mjs` checks the take step for step against pass 1. Flash attention and the 8-bit cache dropped |

Checked in every dry take: all 26 rows shown, 16 payloads equal the capture, the retrieval
inspector's ranking equals the run's search, no local path in any payload or in the app's
text. Length 2:23-2:34 with 5 waits cut (the harness pauses 4 s per model turn).
