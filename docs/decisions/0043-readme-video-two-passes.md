# 0043: The README video, recorded in two passes on Ollama

**Status:** accepted (2026-11-27)

## Context

PLAN_HYBRID.md Phase 9 re-records the README video with the portfolio's "Under the hood"
stage (ported from hl-bead-studio) on the real local model, `qwen3:4b-instruct-2507-q4_K_M`
on Ollama, because the project is local-first. The tooling is in `video/`.

Four live takes failed on the 7.2 GB, CPU-only development laptop. The browser recording
the 1920x1080 stage and qwen3 do not fit together:

| Take | What happened |
|---|---|
| 1 | The browser stopped painting after ~170 s (the display went off, F9) |
| 2 | A model call after the document search hit the client's 300 s timeout (F10, now `OLLAMA_TIMEOUT_S`) |
| 3 | The browser stopped painting when the first answer arrived (memory, F11) |
| 4 | With a 900 s timeout, that model call ran past 900 s: qwen3 reloads after bge-m3 and reads its longest prompt while Edge encodes video on the same CPU |

In the evaluation (decision 0040), with nothing else running, the same calls finish in
under 300 s.

## Decision

Record the video in two passes:

1. **Pass 1, the model** (`video/capture.py`): `start.cmd video`, then the five storyboard
   questions through the app's own chat API on real Ollama, with no browser. Each run's
   stored trace holds the model's outputs word for word and how long each call took.
2. **Pass 2, the recording** (`video/replay_server.py` + `npm run video:ollama`): fresh
   `data-video\` again, and the same app (same settings, ChromaDB, bge-m3 embeddings on
   Ollama, tools, read-only business database, SQL guard, answer check, trace) with one
   change: each agent model call returns what qwen3 returned in pass 1 for that question and
   call, after waiting as long as that call took. qwen3 is not loaded, so the browser has the
   memory and the CPU.

What keeps it honest:

- The model's outputs are qwen3's, unedited; the tools, the search and the answer check run
  live in pass 2. `cues.mjs` fails the take unless every model step, tool call, tool result
  and answer equals pass 1 (`replay_of` in `take.json`).
- Each wait lasts what the model really took, so the video's labels ("waiting for the model:
  154 s, shortened to 1 s") and the inspector's timings are pass 1's real times.
- The stage's banner says the run is replayed, and `docs/video.md` explains the two passes.

## Consequences

- The take takes about twice as long (two passes), and needs no more memory than a
  scripted dry run plus bge-m3.
- The replay lives in `video/`, outside the product; the product code is unchanged by it.
- On a machine with 16 GB or more, a single live pass (`start.cmd video` +
  `npm run video:ollama`, no `VIDEO_REPLAY_DIR`) still works.
