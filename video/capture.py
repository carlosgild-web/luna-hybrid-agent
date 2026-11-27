"""Pass 1 of the README video (decision 0043): the storyboard's questions on the real model,
with no browser and no recorder on the laptop.

Asks each question through the running app (`start.cmd video`, real Ollama) exactly as the
chat does (POST /api/agent/chat, a new conversation each), and saves for each one the SSE
events with their times and the stored trace: the model's outputs, word for word, and how
long each call took. Pass 2 (replay_server.py + `npm run video:ollama`) replays those
outputs through the app while the browser records.

    backend/.venv/Scripts/python video/capture.py --base http://127.0.0.1:8010 --out video/out/pass1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent


def ask(client: httpx.Client, question: str) -> dict:
    events: list[dict] = []
    started = time.monotonic()
    body = {"question": question, "session_id": None, "channel": "intranet"}
    with client.stream("POST", "/api/agent/chat", json=body, timeout=None) as response:
        response.raise_for_status()
        kind = "message"
        for line in response.iter_lines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                events.append({"t_ms": round((time.monotonic() - started) * 1000), "event": kind, "data": data})
                note = data.get("kind") or kind
                print(f"  {events[-1]['t_ms'] / 1000:7.1f} s  {note} {data.get('name', '')}", flush=True)
    trace_id = next((e["data"].get("trace_id") for e in reversed(events) if e["data"].get("trace_id")), None)
    trace = client.get(f"/api/agent/traces/{trace_id}").json() if trace_id else None
    return {"question": question, "wall_ms": round((time.monotonic() - started) * 1000), "events": events, "trace": trace}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--out", default=str(HERE / "out" / "pass1"))
    args = parser.parse_args()
    board = json.loads((HERE / "storyboard-ollama.json").read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    with httpx.Client(base_url=args.base, timeout=30) as client:
        for key, question in board["sentences"].items():
            print(f"== {key}: {question}", flush=True)
            run = ask(client, question)
            (out / f"{key}.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
            trace = run["trace"] or {}
            status = trace.get("status", "no trace")
            print(f"   {status} in {run['wall_ms'] / 1000:.0f} s, model {trace.get('model')}", flush=True)
            if status != "answered":
                failed += 1
    print("pass 1: every question answered" if not failed else f"pass 1: {failed} question(s) not answered")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
