"""The backend for the README GIF and screenshots (decision 0042): the real app with
two stand-ins, so the images are the same on every run and need no Ollama.

- The model is scripted: the agent suite's own replays (`agent-eval --fake`). The
  first call of each run reads the question and replays the case that asks it, word
  for word (`backend/app/eval/agent_questions.yaml`). Any other question gets a short
  "scripted questions only" answer.
- Embeddings are fake (an in-memory vector store), so search ranks by the SQLite FTS5
  half of the hybrid search.

Everything else is real: the agent loop, the tool registry and its argument checks,
the tools, the seeded business database (read only), the SQL guard, the answer check,
the stored trace and the built web app (frontend/dist).

Run: python harness.py   (env: E2E_DATA_DIR, E2E_PORT; default port 8020)
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn
from app.eval.agent_runner import ScriptedLLM, fake_runtime
from app.eval.agent_suite import load_suite
from app.llm.base import ChatTurn, LLMClient
from app.main import create_app

CASES = {case.question.strip(): case for case in load_suite().cases}
STEP_DELAY_S = float(os.environ.get("E2E_STEP_DELAY_MS", "1200")) / 1000
OFF_SCRIPT = "This demo replays the evaluation suite's questions only (scripted model)."


class ByQuestion(LLMClient):
    """One per run: the first call picks the case from the user's question."""

    provider, model = "fake", "scripted"

    def __init__(self) -> None:
        self._case: ScriptedLLM | None = None
        self._off_script = False

    def generate(
        self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192
    ) -> str:
        return OFF_SCRIPT

    def stream_generate(
        self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192
    ):
        yield OFF_SCRIPT

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        num_ctx: int = 8192,
    ) -> ChatTurn:
        if self._case is None and not self._off_script:
            question = next(
                m["content"] for m in reversed(messages) if m["role"] == "user"
            )
            case = CASES.get(question.strip())
            if case is None:
                self._off_script = True
            else:
                self._case = ScriptedLLM(case)
        # A scripted turn takes milliseconds; a pause per turn lets the GIF show each
        # step arrive, as it does on a real model (only the pace is made up).
        time.sleep(STEP_DELAY_S)
        if self._case is None:
            return ChatTurn(content=OFF_SCRIPT, prompt_tokens=100)
        return self._case.chat_with_tools(
            messages, tools, temperature=temperature, num_ctx=num_ctx
        )


def main() -> None:
    data_dir = Path(
        os.environ.get("E2E_DATA_DIR", ROOT / "e2e" / "showcase" / ".tmp" / "data")
    )
    shutil.rmtree(data_dir, ignore_errors=True)
    port = int(os.environ.get("E2E_PORT", "8020"))
    # The inspector names the model of each run: say it is the scripted one.
    os.environ["CHAT_MODEL"] = os.environ["AGENT_MODEL"] = (
        "scripted (agent-eval --fake)"
    )
    with fake_runtime(data_dir) as runtime:
        runtime = dataclasses.replace(
            runtime, llm_client_for=lambda _model: ByQuestion()
        )
        uvicorn.run(
            create_app(runtime), host="127.0.0.1", port=port, log_level="warning"
        )


if __name__ == "__main__":
    main()
