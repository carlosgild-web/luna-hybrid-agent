# 0041: The first release, v2.0.0, and a demo that includes the agent

**Status:** accepted (2026-11-16)

## Context

PLAN_HYBRID.md Phase 8 (portfolio polish): README rewrite, demo video, GIFs, CHANGELOG,
cross-links between the two repos, interview talking points. **Done when** a reviewer
with Docker runs the demo in 5 commands or fewer, and the README explains in 2 minutes
why this is harder than plain RAG.

Checking that path turned up three gaps:

1. **No business data.** **Load the demo company** loaded the 11 documents only. The
   business database came from `python -m app.cli seed-business-db`, so a Docker
   reviewer needed `docker compose exec app ...` first, and without it every
   database question failed.
2. **The wrong agent model.** Demo mode pulled only `qwen2.5:3b`, and the agent falls
   back to `CHAT_MODEL`. That model answered 1 of 3 test questions with the tools'
   numbers (decision 0035). The start scripts had the same problem.
3. **The old artifacts.** `compose.yaml` and the start scripts still downloaded the
   v1.0.1 image and web build from `local-rag-ollama`.

## Decision

- **One click loads both.** The `seed_demo` job (and `app.cli seed-demo`) builds the
  business database first when the file is missing. It never replaces one, so a rebuilt
  or edited database survives a second load. Rebuilding stays `seed-business-db`. The
  seed is written to a temporary file and moved into place, as before, and is audited as
  `business_db_seed`. The agent still only reads it (invariant 9): the seeder is the
  demo loader's, not a tool's.
- **Demo mode pulls the agent model.** `compose.demo.yaml` pulls
  `qwen3:4b-instruct-2507-q4_K_M` as `AGENT_MODEL` (`DEMO_AGENT_MODEL` to change it) and
  keeps `qwen2.5:3b` for the documents chat, since v1's evaluation numbers were measured
  with it. That's about 2.5 GB more, 5.6 GB in all. It also sets
  `OLLAMA_MAX_LOADED_MODELS=1`, which let Phase 7's runs finish on the 7.2 GB laptop.
- **The start scripts save `AGENT_MODEL`** in `.env` (default the same model), download it
  with the others, and pass it to the app. `compose.yaml` passes `AGENT_MODEL` too.
- **Version 2.0.0.** It's the v1.0.1 app with a major new capability, built from the same
  code, so the version continues from it rather than starting again at 1.0.0. With the
  release, `compose.yaml` pulls `ghcr.io/carlosgild-web/luna-hybrid-agent` and the start
  scripts download `web-dist.zip` from this repo's releases.
- **The Ollama image is pinned** to `ollama/ollama:0.40.16`, the version the agent
  evaluation ran on, instead of `latest`.
- **The demo video follows a shot script** (`docs/showcase/video-script.md`, retired in
  Phase 9 with this video; the README video since then is decision 0043). It was recorded on
  the 7.2 GB laptop with a fresh data dir and `qwen3:4b-instruct-2507`. A headless Chrome
  was driven over the DevTools protocol and its screencast frames were cut with ffmpeg, not
  a screen recorder, so the frames are a clean 1280 × 800 without the desktop. Every answer
  is a real first take. Model waits play faster, each with its factor on screen (× 16 to
  × 40: the hybrid answer took 6 minutes), and typing plays at × 3. No answer text is
  edited.
- **Cross-links:** this README says "built on local-rag-ollama", and one line in that
  repo's README points here. That repo gets no other change and no release.

The reviewer's path with only Docker stays at three commands (clone, `cd`, `up`) and one
click.

## Consequences

- Answers in demo mode need about 16 GB of RAM, as in decision 0031. With one model
  loaded at a time, Ollama swaps between the agent model and `bge-m3` on hybrid
  questions, which adds seconds per step but avoids paging.
- A user who already has a business database (from the CLI or an earlier load) keeps it.
  The dashboard says "the business data is ready" only when the click created it.
