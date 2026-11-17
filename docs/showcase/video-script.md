# Demo video: shot script (2–3 minutes)

The video PLAN_HYBRID.md Phase 8 asks for: a hybrid question end to end, with the agent
inspector open. No sound, like v1.0.1's. Speed up long waits and mark them ("× 8").

## Before recording

1. Close Chrome tabs you don't need, Docker Desktop, the Cowork VM and other memory users
   (check `vmmem` in Task Manager). Ollama should have `OLLAMA_MAX_LOADED_MODELS=1`.
2. A fresh data dir, so the dashboard offers the demo:
   ```powershell
   $env:DATA_DIR = "C:\luna-demo-data"
   $env:AGENT_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
   $env:CHAT_MODEL = "qwen2.5:3b"
   cd backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
   ```
3. Browser at 1440 × 900, zoom 100%, light theme. Hide the bookmarks bar.
4. **Warm up** both models with one throwaway agent question before recording, so the
   first take doesn't include the model load.
5. Record with OBS or the Xbox Game Bar (Win + Alt + R), 30 fps.

## Shots

| # | Time | Screen | Action | On-screen caption |
|---|---|---|---|---|
| 1 | 0:00–0:08 | README title | Scroll slowly past the title | "Luna Hybrid Agent: a local agent that decides where the answer lives" |
| 2 | 0:08–0:25 | Console dashboard (`/`) | Click **Load the demo company**; let the bar run (speed up) | "One click: 11 documents and the business database" |
| 3 | 0:25–0:35 | Company site (`/site`) → **Employee login** → **Enter as demo employee** | Open Luna full screen | "The employee portal" |
| 4 | 0:35–0:55 | Luna | Ask **"Which products need restocking?"**. Show the step "Checking the inventory `check_inventory` ✓", then the answer (speed up the wait) | "Database question → typed tool" |
| 5 | 0:55–1:30 | Luna | Ask **"Do CH-2004's Q3 sales match what the Q3 meeting notes say?"**. Show both steps appearing, then the answer with `[1]` (speed up the wait) | "Hybrid: a SQL aggregate and a cited passage" |
| 6 | 1:30–1:40 | Luna | Click `[1]` to open the passage that says 4,120 units | "The passage behind the citation" |
| 7 | 1:40–2:10 | Agent inspector (link under the answer) | Scroll the timeline: model call → `sales_report` (open its rows) → `search_documents` (open the passages) → the answer check, with 4,120 traced to both | "Every step, rebuilt from the stored trace" |
| 8 | 2:10–2:25 | Luna | Ask **"Show me a bar chart of Q3 2026 revenue by month."**. Show the chart, then **Show the data** | "Charts from tool data" |
| 9 | 2:25–2:35 | Luna | Ask **"Delete the orders table."**. Show the refusal | "Read-only, and she refuses" |
| 10 | 2:35–2:45 | README eval table | Hold on the table | "Measured: 53% → 67% on 30 questions, qwen3:4b on a laptop CPU" |

If a take goes wrong (a wrong tool, an answer without the citation), keep it out and ask
again: the README's numbers already say how often that happens. Don't edit an answer's
text.

## After recording

1. Export as H.264 MP4, 1440 × 900 or 1280 × 800, under 25 MB if possible.
2. Save it as `docs/showcase/demo-agent.mp4`, plus a poster frame from shot 7 as
   `docs/showcase/img/agent-demo-play.jpg`.
3. In the README, replace the `<!-- Demo video ... -->` comment under the intro with:
   ```markdown
   [![Watch the tour: a hybrid question end to end, with the agent inspector](docs/showcase/img/agent-demo-play.jpg)](docs/showcase/demo-agent.mp4)
   ```
4. Attach the MP4 to the GitHub release too.
5. **Two stills for the README** (Win + Shift + S, or frames from the recording):
   `docs/showcase/img/40-agent-chat.jpg` (shot 5: the hybrid answer with both steps and
   `[1]`) and `docs/showcase/img/41-agent-inspector.jpg` (shot 7: the timeline with the
   `sales_report` rows open). Then add under the README's example block:
   ```markdown
   | | |
   |---|---|
   | ![Luna's hybrid answer with its steps and citation](docs/showcase/img/40-agent-chat.jpg) **Steps as they happen.** Each tool call, then the cited answer. | ![The agent inspector](docs/showcase/img/41-agent-inspector.jpg) **The agent inspector.** The run rebuilt from its trace, rows and all. |
   ```
