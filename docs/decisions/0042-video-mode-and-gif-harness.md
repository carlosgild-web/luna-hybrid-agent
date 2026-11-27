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
