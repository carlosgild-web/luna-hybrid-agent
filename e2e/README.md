# End-to-end test

This tests the running app with a real Ollama and the sample corpus in `corpus/`, which is fictional
"Hilda & Luna Beadworks" data. It works through the same HTTP API the web UI uses.

It checks:
- ingestion: PDF, Word including tables, text and Markdown
- answers, against facts in `scenario.json`
- citations
- grounding: off-topic and missing facts must give "not found in the documents"
- shelf isolation
- versions, rollback and diff
- trash and restore
- review before upload (`/api/documents/check`), the troubleshooter checks (`/api/diagnostics`), and suggested shelves (`/api/intake`): re-dropping the corpus finds only duplicates and indexes nothing, and a new document is suggested for the right shelf
- jobs: cancel and retry
- reindex and blue/green rebuild
- prompt injection
- persistence across a restart

The unit tests in `backend/tests/` use fakes. This test covers what they can't: real files, the real
model and real memory limits. It is not run in CI.

## Run it

```bash
# 1. Start the server on an EMPTY data directory, logging to a file
cd backend
DATA_DIR=./data-e2e .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 > e2e-server.log 2>&1 &

# 2. Run the main phases (about 1–3 hours on a CPU-only laptop)
cd ..
backend/.venv/Scripts/python.exe e2e/run_e2e.py --run-dir backend/data-e2e/e2e-runs/run1 \
    --server-log backend/e2e-server.log

# 3. Optional restart check: queue an upload, kill and restart the server, verify
backend/.venv/Scripts/python.exe e2e/run_e2e.py --run-dir ... --phases persistence_prepare
#    (stop the server now, start it again)
backend/.venv/Scripts/python.exe e2e/run_e2e.py --run-dir ... --phases persistence_verify report
```

The report goes to `<run-dir>/report.md`. The exit code is non-zero if any **must** check fails: an
app mechanic such as ingestion, versions, rollback, trash, jobs or persistence. **Quality** checks
cover what the model answers. They are reported as a pass rate, and each failure is classified:
- **retrieval miss:** the fact was not in the passages found
- **answer miss:** the fact was found but not stated
- **app error**

## Files

| File | What it is |
|---|---|
| `corpus/` | 11 sample documents, in the folder layout they were delivered in |
| `scenario.json` | Collections, questions with expected facts, grounding tests, edits, new documents |
| `variants.py` | Builds edited copies (new versions) and new documents; the corpus is never modified |
| `run_e2e.py` | The runner and report writer |

## The README GIF and screenshots (`showcase/`)

A separate Playwright project, not a test of the model: the real app and web UI on the agent
suite's scripted model (decision 0042). It needs the backend venv, Node, Microsoft Edge and, for
the GIF, ffmpeg. No Ollama.

```bash
cd e2e/showcase
npm install
npm run showcase       # builds the web app, writes docs/showcase/img/50-55-*.png
npm run showcase:gif   # builds the web app, writes docs/showcase/demo.gif (~30 s, ~4 MB)
```

`harness.py` serves the app on port 8020 from a scratch folder (`e2e/showcase/.tmp/`). Each
question is matched, word for word, to a case in `backend/app/eval/agent_questions.yaml`, and its
`fake` run is replayed, with a 1.2 s pause per model turn (`E2E_STEP_DELAY_MS`) so the steps
appear one by one. Embeddings are fake, so search ranks by its keyword half. Everything else is
the real app: the agent loop, the registry's argument checks, the tools, the read-only business
database, the SQL guard, the answer check and the stored trace.
