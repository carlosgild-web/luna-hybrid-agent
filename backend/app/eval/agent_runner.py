"""Runs the agent evaluation suite (decision 0040).

Two ways, the same loop, tools and scoring:

- **Real**: each question goes to Luna on Ollama with the app's documents and
  business database, exactly as the chat runs it, and every trace is stored, so
  the inspector can open any case.
- **Fake** (`--fake`, CI): a scripted model replays each case's `fake` run against
  the real tools, in a scratch data dir with the demo documents (fake embeddings,
  so keyword search finds the passages) and the seeded business database. It
  needs no Ollama and checks the suite itself: every expected value must come out
  of the real tools.

External APIs are always off: `fx_rate` answers with the stored rate, so a run
is reproducible and never calls out (invariant 10).
"""

import datetime as dt
import json
import re
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.agent.business_seed import seed_business_db
from app.agent.loop import AgentLimits, run_agent
from app.agent.registry import ToolContext
from app.agent.tools import build_registry
from app.agent.trace import AgentTrace, ToolStep
from app.core.config import Settings
from app.core.runtime import Runtime
from app.eval.agent_suite import AgentCase, AgentSuite, CaseResult, score_case, summarize
from app.eval.judge import JUDGE_INSTRUCTIONS, parse_verdict
from app.llm.base import ChatTurn, LLMClient, ToolCall
from app.retrieval.library import DocumentLibrary

REPORT_VERSION = 1
FAKE_MODEL = "scripted"
_CITE = re.compile(r"\{cite:([^}]+)\}")
JUDGE_SOURCE_CHARS = 3000  # per tool result, so the judge prompt fits the context


class ScriptError(RuntimeError):
    """A case's `fake` run can't be replayed (a bad step, or a cited file no
    search showed)."""


class ScriptedLLM(LLMClient):
    """Replays one case's `fake` run: `[tool, {arguments}]` is a tool call, a string
    is the answer. `{cite:<file>}` in the answer becomes the number of the first
    passage the run showed from that file, as a model reading the results would
    cite it. The judge's `generate` always says YES."""

    def __init__(self, case: AgentCase) -> None:
        self.case = case
        self.steps = list(case.fake)

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        num_ctx: int = 8192,
    ) -> ChatTurn:
        if not self.steps:
            raise ScriptError(f"{self.case.id}: the fake run ended without an answer")
        step = self.steps.pop(0)
        if isinstance(step, str):
            return ChatTurn(content=self._cite(step, messages), prompt_tokens=100)
        if isinstance(step, list) and len(step) == 2 and isinstance(step[1], dict):
            return ChatTurn(tool_calls=[ToolCall(str(step[0]), step[1])], prompt_tokens=100)
        raise ScriptError(f"{self.case.id}: a fake step must be [tool, {{args}}] or text")

    def _cite(self, text: str, messages: list[dict[str, Any]]) -> str:
        shown: dict[str, str] = {}
        for message in messages:
            if message.get("role") != "tool" or message.get("tool_name") != "search_documents":
                continue
            for passage in json.loads(message["content"]).get("passages", []):
                shown.setdefault(passage["document"], passage["cite"])

        def number(match: re.Match[str]) -> str:
            filename = match.group(1)
            if filename not in shown:
                raise ScriptError(
                    f"{self.case.id}: no search showed a passage from {filename!r} "
                    f"(shown: {', '.join(shown) or 'none'})"
                )
            return shown[filename]

        return _CITE.sub(number, text)

    def generate(self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192) -> str:
        return "YES"

    def stream_generate(self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192):
        yield self.generate(prompt)


# ------------------------------------------------------------------ running


def run_suite(
    suite: AgentSuite,
    cases: list[AgentCase],
    *,
    runtime: Runtime,
    model: str,
    llm_for: Callable[[AgentCase], LLMClient],
    limits: AgentLimits,
    judge: LLMClient | None = None,
    save: Callable[[AgentTrace], None] | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Runs `cases` one after the other. `save` stores each trace (and sets its id)."""
    settings = runtime.settings.model_copy(update={"allow_external_apis": False})
    documents = DocumentLibrary.from_runtime(runtime)
    registry = build_registry(documents)
    results: list[CaseResult] = []
    for case in cases:
        trace = run_agent(
            case.question,
            llm=llm_for(case),
            registry=registry,
            ctx=ToolContext(settings, documents=documents),
            limits=limits,
            model=model,
            today=suite.today,
        )
        if save is not None:
            save(trace)
        result = score_case(case, trace)
        if judge is not None and trace.answer and trace.status != "llm_error":
            result.judge_score, result.judge_note = judge_answer(judge, trace, limits)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def judge_prompt(trace: AgentTrace) -> str:
    """The RAG judge's instructions (decision 0019) over the run's tool results."""
    sources = [
        f"[{n}] {step.name}: {step.result[:JUDGE_SOURCE_CHARS]}"
        for n, step in enumerate(
            (s for s in trace.steps if isinstance(s, ToolStep) and s.ok), start=1
        )
    ]
    return (
        f"{JUDGE_INSTRUCTIONS}\n\nSources:\n"
        + ("\n\n".join(sources) or "(none)")
        + f"\n\nAnswer to check:\n{trace.answer.strip()}\n\nVerdict:"
    )


def judge_answer(
    llm: LLMClient, trace: AgentTrace, limits: AgentLimits
) -> tuple[float | None, str]:
    try:
        reply = llm.generate(judge_prompt(trace), temperature=0.0, num_ctx=limits.num_ctx)
    except Exception as exc:  # the judge is a hint; its failure never stops the run
        return None, f"judge failed: {type(exc).__name__}: {exc}"
    return parse_verdict(reply)


# ------------------------------------------------------------------ fake mode


@contextmanager
def fake_runtime(data_dir: Path) -> Iterator[Runtime]:
    """A runtime on fakes in `data_dir`: migrated SQLite, in-memory vectors, fake
    embeddings, the demo documents and the seeded business database."""
    from app.db.migrate import upgrade_to_head
    from app.db.session import make_engine, make_session_factory
    from app.llm.fake_client import FakeEmbeddingClient, FakeLLMClient
    from app.llm.ollama_admin import OllamaAdmin
    from app.retrieval.fake_store import FakeVectorStore
    from app.versioning.demo import seed_demo

    settings = Settings(_env_file=None, data_dir=data_dir, allow_external_apis=False)
    settings.ensure_data_dirs()
    upgrade_to_head(settings.db_path)
    engine = make_engine(settings.db_path)
    embedder = FakeEmbeddingClient(dimensions=8)
    runtime = Runtime(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        vector_store=FakeVectorStore(),
        embedding_client_for=lambda _model: embedder,
        llm_client_for=lambda _model: FakeLLMClient(),
        ollama=OllamaAdmin(settings.ollama_base_url),
    )
    try:
        with runtime.session_factory() as db:
            seed_demo(
                db,
                settings=settings,
                embedding_client_for=runtime.embedding_client_for,
                vector_store=runtime.vector_store,
            )
        seed_business_db(settings.business_db_file)
        yield runtime
    finally:
        engine.dispose()


# ------------------------------------------------------------------ the report


def build_report(
    results: list[CaseResult],
    *,
    label: str,
    model: str,
    limits: AgentLimits,
    started_at: dt.datetime,
    fake: bool,
    judge_model: str | None,
) -> dict[str, Any]:
    return {
        "version": REPORT_VERSION,
        "label": label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "config": {
            "model": model,
            "fake": fake,
            "judge_model": judge_model,
            "commit": _git_commit(),
            "max_iterations": limits.max_iterations,
            "token_budget": limits.token_budget,
            "temperature": limits.temperature,
            "num_ctx": limits.num_ctx,
            "external_apis": False,
        },
        "metrics": summarize(results),
        "cases": [r.to_dict() for r in results],
    }


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def write_report(report: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report["started_at"].replace(":", "").replace("-", "")[:15]
    slug = re.sub(r"[^a-z0-9]+", "-", report["label"].lower()).strip("-")[:40]
    path = directory / f"{stamp}-{slug or 'run'}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None
