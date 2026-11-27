"""Pass 2 of the README video (decision 0043): the real app, with the model's outputs from
pass 1 replayed instead of a live model call.

The app is the one `start.cmd video` runs: the same settings, data-video/, ChromaDB, bge-m3
embeddings on Ollama, the tools, the read-only business database, the SQL guard, the answer
check and the stored trace. Only the agent's model calls are replaced: each one returns what
qwen3 returned in pass 1 for that question and call, word for word, after waiting as long as
that call took (VIDEO_REPLAY_SPEED scales it, for tests). qwen3 itself is not loaded, so the
browser and the recorder have the laptop's memory and CPU.

cues.mjs checks that every tool call and result of the take equals pass 1.

    set DATA_DIR=...\\data-video  (and the other settings start.cmd video uses)
    backend/.venv/Scripts/python video/replay_server.py --pass1 video/out/pass1 --port 8010
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import os

import uvicorn
from app.core.config import get_settings
from app.core.runtime import build_runtime
from app.llm.base import ChatTurn, LLMClient, ToolCall
from app.main import create_app

SPEED = float(os.environ.get("VIDEO_REPLAY_SPEED", "1"))


class Replay(LLMClient):
    """One per run: the first call finds pass 1's run for the question, then each call
    returns the next model step of that run."""

    def __init__(self, runs: dict[str, list[dict[str, Any]]], model: str) -> None:
        self.runs = runs
        self.provider, self.model = "ollama-replay", model
        self.steps: list[dict[str, Any]] | None = None

    def generate(
        self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192
    ) -> str:
        raise RuntimeError("the replay only answers the agent's model calls")

    def stream_generate(
        self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192
    ):
        raise RuntimeError("the replay only answers the agent's model calls")

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        num_ctx: int = 8192,
    ) -> ChatTurn:
        if self.steps is None:
            question = next(
                m["content"] for m in messages if m["role"] == "user"
            ).strip()
            if question not in self.runs:
                raise RuntimeError(f"pass 1 has no run for {question!r}")
            self.steps = list(self.runs[question])
        if not self.steps:
            raise RuntimeError("pass 1's run had no more model calls")
        step = self.steps.pop(0)
        time.sleep(step["ms"] / 1000 * SPEED)
        calls = (
            []  # the model wrote them in its text: the loop parses them again, as in pass 1
            if step.get("parsed_from_content")
            else [ToolCall(c["name"], c.get("arguments")) for c in step["tool_calls"]]
        )
        return ChatTurn(
            content=step["content"],
            tool_calls=calls,
            prompt_tokens=step["prompt_tokens"],
            completion_tokens=step["completion_tokens"],
        )


def load_runs(pass1: Path) -> tuple[dict[str, list[dict[str, Any]]], str]:
    runs: dict[str, list[dict[str, Any]]] = {}
    models = set()
    for path in sorted(pass1.glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        trace = run.get("trace") or {}
        if trace.get("status") != "answered":
            raise SystemExit(
                f"{path.name}: pass 1 did not answer ({trace.get('status')})"
            )
        runs[run["question"].strip()] = [
            s for s in trace["steps"] if s["kind"] == "llm"
        ]
        models.add(trace["model"])
    if len(models) != 1:
        raise SystemExit(f"pass 1 used {sorted(models)}: expected one model")
    return runs, models.pop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pass1", default=str(Path(__file__).resolve().parent / "out" / "pass1")
    )
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    runs, model = load_runs(Path(args.pass1))
    settings = get_settings()
    if settings.agent_chat_model != model:
        raise SystemExit(
            f"AGENT_MODEL is {settings.agent_chat_model}, pass 1 used {model}"
        )
    runtime = build_runtime(settings)
    runtime = dataclasses.replace(
        runtime, llm_client_for=lambda _m: Replay(runs, model)
    )
    print(
        f"replaying {len(runs)} runs of {model} from {args.pass1} (speed {SPEED})",
        flush=True,
    )
    uvicorn.run(
        create_app(runtime), host="127.0.0.1", port=args.port, log_level="warning"
    )


if __name__ == "__main__":
    main()
