# 0042: A video mode for the start scripts, and a harness for the README GIF

**Status:** accepted (2026-11-27)

## Context

PLAN_HYBRID.md Phase 9 brings this repo to the portfolio's showcase standard: a README
GIF and a re-recorded video with an "Under the hood" panel, on the real local model.
Both need the app to start from the same state every time, without touching the
user's own `data\` folder. Until now the only way to that state was a fresh install
and a click on **Load the demo company**.

## Decision: `start.cmd video` (and `./start.sh video`)

- **Its own folder and port.** `DATA_DIR` is `data-video\` (gitignored) and the app
  listens on port 8010, so a normal start on 8000 and its library are left alone.
- **Wiped at every start.** The last video server is stopped (its `app.pid`), the
  folder is emptied (except this run's `start.log`), then the app starts. Two starts
  give the same documents, chunks, glossary and business database (checked by hash).
- **The dashboard's own load.** The demo company is loaded with `POST /api/demo/seed`,
  the job behind the button, so the take shows what a visitor gets: the 11 documents
  embedded with `bge-m3`, the glossary, and the business database (decision 0041).
- **One route, no questions.** Python and the Ollama on this computer only (no Docker),
  no prompts. It fails with the fix when Ollama, `bge-m3` or the agent model is missing.
- **One model.** `CHAT_MODEL` and `AGENT_MODEL` are both
  `qwen3:4b-instruct-2507-q4_K_M` (decision 0035), so only it and `bge-m3` are ever
  loaded. `ALLOW_EXTERNAL_APIS=false`: the FX answer uses the stored rate. The script
  warms the model (one short chat, `keep_alive` 30 minutes) and suggests
  `OLLAMA_MAX_LOADED_MODELS=1` when it isn't set.
- `start.cmd video stop` stops it. `video` works with `start` and `stop` only.

## Consequences

- The video tooling (`video/`) can assume `http://127.0.0.1:8010` and a known state.
- Product code is unchanged; only the start scripts and `.gitignore`.
- `./start.sh video` follows the same steps. It was checked for syntax here (Windows)
  and is not run in CI, like the rest of the Python route on macOS and Linux.

## Decision: the README GIF runs on the evaluation suite's scripted model

- **What is scripted:** the model and the embeddings. `e2e/showcase/harness.py` builds the
  agent suite's fake runtime (`agent_runner.fake_runtime`: migrated SQLite, in-memory vectors,
  fake embeddings, the demo documents, the seeded business database) and gives every run a
  model that replays the suite case asking the same question (`ScriptedLLM`, the replay
  `agent-eval --fake` uses in CI). The inspector names it "scripted (agent-eval --fake)".
- **What is real:** the app and web UI as shipped, the agent loop, the tool registry, the
  tools, the SQL guard, the answer check, the trace and the inspector. No product code was
  changed for it.
- **Paced.** Scripted turns take milliseconds, so the harness waits 1.2 s per model turn and
  the GIF shows the steps arrive one by one. The inspector's timings are that pause.
- **Why not real `bge-m3`:** the GIF must be the same on every run and must not need Ollama.
  With fake embeddings the search ranks by FTS5; the cited passage is still the meeting
  notes, the only document that holds the figure.
- **Edge**, the browser already on Windows (`channel: 'msedge'`), so Playwright downloads
  nothing. Playwright is a dev dependency of `e2e/showcase/` only.
- In the GIF and the hybrid screenshot, the passage line with the cited number is marked
  (a highlight; the text is unchanged).

## A bug found on the way

A scripted run ends in milliseconds, and its answer vanished from the chat. At the end of a
stream the chat reloads the session with `fetchQuery`, which returned the copy cached at the
`session` event (question only) when the run ended within the app's 2 s `staleTime`. Real
answers on a warm model can be that fast too (a refusal). Fixed in `useChatStream.ts`
(`staleTime: 0`). The frontend has no unit-test runner, so the check is the showcase run with
`E2E_STEP_DELAY_MS=0`.
