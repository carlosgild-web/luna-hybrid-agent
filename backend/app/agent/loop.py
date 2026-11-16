"""The agent loop: Luna calls tools until she can answer (decision 0032, 0035).

One run is a bounded conversation with the model on Ollama's `tools` API:

1. The model gets the system prompt, the question and the tool declarations.
2. Each turn it either answers in text (the run ends) or asks for tool calls; the loop
   runs them, each in a worker thread with a timeout, and sends the results back. An
   empty reply is retried once.
3. A limit ends the tool phase: AGENT_MAX_ITERATIONS model calls, AGENT_TOKEN_BUDGET
   tokens, or the same tool failing twice in a row. Then one last call, with no tools
   offered, answers from what the run has so far.

Everything lands in an `AgentTrace` (invariant 8), with each tool result exactly as
the model saw it (invariant 11). Once the model answers, `agent/answer.py` checks the
answer's citations and numbers against the trace. The loop stores nothing itself;
callers save the trace through `versioning/agent_traces.py`.
"""

import datetime as dt
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.agent.answer import check_answer, drop_unbacked_citations
from app.agent.registry import ToolContext, ToolRegistry, ToolResult
from app.agent.trace import AgentTrace, LLMStep, Status, Step, ToolStep
from app.core.config import Settings
from app.llm.base import ChatTurn, LLMClient, ToolCall

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Luna, the assistant of Hilda & Luna Beadworks, a small company that designs and \
sells beaded jewelry. You answer questions about the business with the tools you are given.{tools}

Rules:
- Every number in your answer (stock, units, money, dates, percentages) must come from a \
tool result in this conversation. Never use numbers from memory and never estimate.
- {when}{extra}
- If a tool returns an error, read it, fix the arguments and try once more.
- If the tools don't have what the question asks, say so plainly instead of guessing.
- Answer in the language of the question. Be brief and name products with their SKU.

Today is {today}."""

WHEN_DATABASE_ONLY = (
    "When the question needs business data, call a tool. You may call several tools."
)

# With search_documents offered (Phase 4): which source answers what, and how to cite.
HYBRID_TOOLS = """ search_documents finds what the company's documents say (policies, \
procedures, agreements, meeting notes); the other tools read the business database (stock, \
sales, purchases)."""
HYBRID_WHEN = """\
Call the tool that has the facts: search_documents for what a document says, the database \
tools for stock, sales and purchases. A question that compares the two needs both. You may \
call several tools.
- The documents cover HR policies (time off, benefits, holidays), workshop safety, \
customer questions, shipping and returns, products and prices, supplier agreements and \
meeting notes. For any question about these, call search_documents first. Never say the \
documents don't have something before you have searched them.
- Cite each fact from a document with its passage number, e.g. [2]. Facts from the database \
get no number: say they come from the database (the sales records, the inventory).
- If a document and the database disagree, say so and give both figures."""

# Phase 5: added when fx_rate / make_chart are offered.
FX_RULE = """
- For an exchange rate, or to convert between USD and MXN, call fx_rate (with amount to \
convert) instead of doing the arithmetic. If its source is "stored", say that the rate is \
the one stored in the company's books, not today's market rate."""
CHART_RULE = """
- Call make_chart only when the user asks for a chart or a graph, after the tool that has \
the numbers, and copy its values exactly. Never write a chart as JSON, code or a table \
in your answer: only make_chart draws charts."""

FINAL_CALL = (
    "Stop calling tools now. Answer the question with the tool results above. If they "
    "don't contain what the question needs, say what is missing. Use only numbers that "
    "appear in the tool results."
)

# Ollama 0.40 sometimes drops a tool call its parser can't read and returns neither text
# nor calls (seen with qwen2.5:3b). The same prompt fails again, so the retry adds this.
EMPTY_REPLY = "Your last reply was empty. Call one of the tools, or answer the question in text."

NO_ANSWER = (
    "I couldn't put an answer together within the agent's limits. "
    "The steps I took are in the trace."
)

OnStep = Callable[[Step], None]

DOCUMENTS_TOOL = "search_documents"
FX_TOOL = "fx_rate"
CHART_TOOL = "make_chart"


@dataclass(frozen=True)
class AgentLimits:
    max_iterations: int = 6  # model calls, the final no-tools call included
    tool_timeout_s: float = 20.0
    token_budget: int = 32_000  # prompt + completion tokens over the whole run
    temperature: float = 0.0
    num_ctx: int = 8192

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")

    @classmethod
    def from_settings(cls, settings: Settings) -> "AgentLimits":
        return cls(
            max_iterations=settings.agent_max_iterations,
            tool_timeout_s=settings.agent_tool_timeout_s,
            token_budget=settings.agent_token_budget,
            temperature=settings.agent_temperature,
            num_ctx=settings.ollama_num_ctx,
        )


def system_prompt(
    today: dt.date, *, documents: bool = False, fx: bool = False, charts: bool = False
) -> str:
    """Each flag says a tool is offered and adds its rules: `documents` the citation
    rules, `fx` the stored-rate disclosure, `charts` when to draw one."""
    return SYSTEM_PROMPT.format(
        today=today.isoformat(),
        tools=HYBRID_TOOLS if documents else "",
        when=HYBRID_WHEN if documents else WHEN_DATABASE_ONLY,
        extra=(FX_RULE if fx else "") + (CHART_RULE if charts else ""),
    )


def prompt_for(registry: ToolRegistry, today: dt.date) -> str:
    """The system prompt for the tools `registry` offers."""
    names = registry.names
    return system_prompt(
        today,
        documents=DOCUMENTS_TOOL in names,
        fx=FX_TOOL in names,
        charts=CHART_TOOL in names,
    )


def run_agent(
    question: str,
    *,
    llm: LLMClient,
    registry: ToolRegistry,
    ctx: ToolContext,
    limits: AgentLimits | None = None,
    model: str = "",
    on_step: OnStep | None = None,
    today: dt.date | None = None,
) -> AgentTrace:
    """Answers `question` with the tools in `registry`. Never raises for the model's
    or a tool's failure: the trace's status and error say what happened."""
    run = _Run(llm, registry, ctx.for_new_run(), limits or AgentLimits(), on_step)
    run.trace = AgentTrace(question=question, model=model)
    today = today or dt.date.today()
    started = time.perf_counter()
    try:
        run.execute(question, today)
    finally:
        run.trace.duration_ms = _ms_since(started)
    if run.trace.status != "llm_error":
        removed = drop_unbacked_citations(run.trace)
        run.trace.checks = check_answer(run.trace, today).to_dict()
        if removed:
            run.trace.checks["removed_citations"] = removed
    return run.trace


class _Run:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        ctx: ToolContext,
        limits: AgentLimits,
        on_step: OnStep | None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.ctx = ctx
        self.limits = limits
        self.on_step = on_step
        self.trace: AgentTrace
        self.messages: list[dict[str, Any]] = []
        self.results: dict[str, ToolResult] = {}  # same call -> same result
        self.last_call_failed: dict[str, bool] = {}  # per tool name
        self.stop: Status | None = None  # why tools are no longer offered
        self.retried_empty = False

    def execute(self, question: str, today: dt.date) -> None:
        self.messages = [
            {
                "role": "system",
                "content": prompt_for(self.registry, today),
            },
            {"role": "user", "content": question},
        ]
        for iteration in range(1, self.limits.max_iterations + 1):
            if self.stop is None and self.trace.total_tokens >= self.limits.token_budget:
                self.stop = "token_budget"
            elif self.stop is None and iteration == self.limits.max_iterations:
                self.stop = "max_iterations"
            final = self.stop is not None
            if final:
                # As the user: chat templates such as qwen2.5's render only the first
                # system message, at the top.
                self.messages.append({"role": "user", "content": FINAL_CALL})

            turn, ms = self._chat(final)
            if turn is None:
                return

            calls, parsed = turn.tool_calls, False
            if not calls:
                calls = parse_calls_from_content(turn.content, self.registry.names)
                parsed = bool(calls)
            requested = [{"name": c.name, "arguments": _as_dict(c.arguments)} for c in calls]
            self._emit(
                LLMStep(
                    iteration=iteration,
                    content=turn.content,
                    tool_calls=requested,
                    prompt_tokens=turn.prompt_tokens,
                    completion_tokens=turn.completion_tokens,
                    ms=ms,
                    tools_offered=not final,
                    parsed_from_content=parsed,
                )
            )

            if not final and not calls and not turn.content.strip() and not self.retried_empty:
                self.retried_empty = True  # once per run; the empty turn isn't sent back
                self.messages.append({"role": "user", "content": EMPTY_REPLY})
                continue

            if final or not calls:
                # A final turn that still asks for tools has no answer to show.
                answer = "" if calls else turn.content.strip()
                self.trace.answer = answer or NO_ANSWER
                self.trace.status = self.stop or "answered"
                return

            self.messages.append(
                {
                    "role": "assistant",
                    "content": "" if parsed else turn.content,
                    "tool_calls": [{"function": call} for call in requested],
                }
            )
            for call in calls:
                self._call_tool(iteration, call)

    def _chat(self, final: bool) -> tuple[ChatTurn | None, int]:
        started = time.perf_counter()
        try:
            turn = self.llm.chat_with_tools(
                self.messages,
                [] if final else self.registry.ollama_tools(),
                temperature=self.limits.temperature,
                num_ctx=self.limits.num_ctx,
            )
        except Exception as exc:  # Ollama down, out of memory, a bad reply: all in the trace
            logger.warning("Agent model call failed: %s", exc)
            self.trace.status = "llm_error"
            self.trace.error = f"{type(exc).__name__}: {exc}"
            return None, _ms_since(started)
        return turn, _ms_since(started)

    def _call_tool(self, iteration: int, call: ToolCall) -> None:
        arguments = _as_dict(call.arguments)
        key = call.name + json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        reused = key in self.results
        if reused:
            result, ms = self.results[key], 0
        else:
            result, ms = self._run_with_timeout(call)
            self.results[key] = result

        shown = result.to_message()
        self._emit(
            ToolStep(
                iteration=iteration,
                name=call.name,
                arguments=arguments,
                ok=result.ok,
                result=shown,
                ms=ms,
                reused=reused,
                meta=result.meta,
            )
        )
        self.messages.append({"role": "tool", "tool_name": call.name, "content": shown})

        if not result.ok and self.last_call_failed.get(call.name) and self.stop is None:
            self.stop = "tool_errors"
        self.last_call_failed[call.name] = not result.ok

    def _run_with_timeout(self, call: ToolCall) -> tuple[ToolResult, int]:
        """Runs the tool in a daemon thread. A tool that overruns is left to finish on
        its own (Python can't stop a thread); its result is dropped."""
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["result"] = self.registry.execute(call.name, call.arguments, self.ctx)
            except Exception as exc:  # a bug in a handler; the model only sees its type
                box["error"] = exc

        started = time.perf_counter()
        worker = threading.Thread(target=target, name=f"agent-tool-{call.name}", daemon=True)
        worker.start()
        worker.join(self.limits.tool_timeout_s)
        ms = _ms_since(started)
        arguments = _as_dict(call.arguments)

        if worker.is_alive():
            logger.warning("Tool %s timed out after %s s", call.name, self.limits.tool_timeout_s)
            error = (
                f"The tool didn't answer within {self.limits.tool_timeout_s:g} seconds. "
                "Try narrower arguments."
            )
            return ToolResult(call.name, arguments, ok=False, error=error), ms
        if "error" in box:
            exc = box["error"]
            logger.error("Tool %s raised", call.name, exc_info=exc)
            error = f"The tool failed with an internal error ({type(exc).__name__})."
            return ToolResult(call.name, arguments, ok=False, error=error), ms
        return box["result"], ms

    def _emit(self, step: Step) -> None:
        self.trace.steps.append(step)
        if self.on_step is not None:
            self.on_step(step)


# --------------------------------------------------- tool calls written as text

_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse_calls_from_content(content: str, known: list[str]) -> list[ToolCall]:
    """Tool calls a model wrote as JSON in its text instead of using the tools API.

    Accepts `{"name": ..., "arguments": {...}}` (or `parameters`), a list of those, the
    same wrapped in a ```json fence or in `<tool_call>` tags, and the
    `{"function": {...}}` shape. Every call must name a known tool; otherwise the text
    is taken as an answer and nothing is parsed.
    """
    text = (content or "").strip()
    if not text:
        return []
    tagged = _TOOL_CALL_TAG.findall(text)
    chunks = tagged or [text]
    calls: list[ToolCall] = []
    for chunk in chunks:
        fenced = _FENCE.match(chunk)
        if fenced:
            chunk = fenced.group(1)
        if not chunk or chunk[0] not in "[{":
            return []
        try:
            data = json.loads(chunk)
        except ValueError:
            return []
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                return []
            if isinstance(item.get("function"), dict):
                item = item["function"]
            name = item.get("name")
            if name not in known:
                return []
            calls.append(ToolCall(name, item.get("arguments", item.get("parameters"))))
    return calls


def _as_dict(arguments: Mapping[str, Any] | str | None) -> dict[str, Any]:
    """The arguments as a dict for the trace and for the assistant message Ollama
    expects back; a string that isn't a JSON object becomes {}."""
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
