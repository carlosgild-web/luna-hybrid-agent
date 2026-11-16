"""The agent evaluation: the suite, the score of one run, the totals (decision 0040).

`agent_questions.yaml` holds the cases (PLAN_HYBRID.md section 5). A case is scored
from its trace alone, with the same answer check the chat shows (`agent/answer.py`),
so a stored trace can be scored again. Scoring is pure: `agent_runner.py` runs the
questions and `cli.py agent-eval` prints the table.

What a case checks, each a reason it fails:

- **tools**: every expected tool was called (one of each group of alternatives),
  and no forbidden one was
- **values**: the answer has each expected fact and states each expected number
- **grounded**: no number in the answer is missing from the tool results, the
  citations point at passages the run showed, and at the expected documents
- **refusal**: a refusal case refuses or says the data isn't there
- **chart**: a chart case drew a chart whose values all come from tool results
- **no JSON**: the answer contains no JSON or code (a chart written as text)
- **status**: the run ended with an answer, not at a limit or on a model error

Two more measures don't fail a case but explain a failure: whether the expected
numbers were in some tool result (the right data was fetched, even if the answer
got it wrong) and the optional LLM judge.
"""

import datetime as dt
import re
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.agent.answer import states_value, tools_with_value
from app.agent.trace import AgentTrace, LLMStep, ToolStep
from app.eval.metrics import missing_facts
from app.retrieval.citations import INVALID, MISSING
from app.retrieval.textnorm import normalize

SUITE_PATH = Path(__file__).with_name("agent_questions.yaml")
TIERS = ("doc", "db", "external", "hybrid", "refusal")
CHART_TOOL = "make_chart"

# A refusal, or "the data isn't there", in English or Spanish; matched after
# `normalize` (lowercase, no accents). Loose on purpose: a refusal that invents a
# number still fails, on the grounded check.
REFUSAL_MARKERS = (
    "can't",
    "cannot",
    "can not",
    "unable",
    "not able",
    "won't",
    "don't have",
    "do not have",
    "doesn't have",
    "does not have",
    "don't give",
    "doesn't give",
    "does not give",
    "don't say",
    "doesn't say",
    "does not say",
    "don't mention",
    "doesn't mention",
    "not mentioned",
    "not specified",
    "no data",
    "no sales",
    "no orders",
    "no record",
    "no information",
    "not available",
    "isn't available",
    "not in the",
    "isn't in the",
    "there is no",
    "there are no",
    "covers",
    "read-only",
    "read only",
    "not allowed",
    "not possible",
    "only read",
    "no puedo",
    "no tengo",
    "no hay",
    "no esta",
    "no se puede",
    "no dispongo",
    "no contiene",
    "no incluye",
    "no se menciona",
    "no existe",
    "solo lectura",
    "solo puedo leer",
)

# JSON or code in an answer: a fence, or an object with a quoted key.
_JSON_IN_ANSWER = re.compile(r"```|\{\s*\"")


class InvalidSuiteError(ValueError):
    pass


@dataclass(frozen=True)
class AgentCase:
    id: str
    tier: str
    question: str
    tools: list[list[str]] = field(default_factory=list)  # each group: call one of these
    forbid: list[str] = field(default_factory=list)
    facts: list[list[str]] = field(default_factory=list)
    numbers: list[Decimal] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    refuse: bool = False
    chart: bool = False
    fake: list[Any] = field(default_factory=list)  # the scripted run (agent_runner.py)


@dataclass(frozen=True)
class AgentSuite:
    today: dt.date
    cases: list[AgentCase]


_CASE_KEYS = {f for f in AgentCase.__dataclass_fields__}


def load_suite(path: Path = SUITE_PATH) -> AgentSuite:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise InvalidSuiteError(f"{path.name}: expected a mapping with a 'cases' list")
    today = data.get("today") or dt.date.today()
    if not isinstance(today, dt.date):
        raise InvalidSuiteError(f"{path.name}: 'today' must be a date, YYYY-MM-DD")
    cases = [_case(raw, n) for n, raw in enumerate(data["cases"], start=1)]
    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise InvalidSuiteError(f"{path.name}: duplicate case ids: {', '.join(duplicates)}")
    return AgentSuite(today=today, cases=cases)


def _case(raw: Any, n: int) -> AgentCase:
    where = f"case {n}"
    if not isinstance(raw, dict):
        raise InvalidSuiteError(f"{where}: expected a mapping")
    where = f"case {raw.get('id', n)!r}"
    unknown = set(raw) - _CASE_KEYS
    if unknown:
        raise InvalidSuiteError(f"{where}: unknown keys {sorted(unknown)}")
    for key in ("id", "tier", "question"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise InvalidSuiteError(f"{where}: '{key}' is required")
    if raw["tier"] not in TIERS:
        raise InvalidSuiteError(f"{where}: tier must be one of {', '.join(TIERS)}")
    try:
        return AgentCase(
            id=raw["id"],
            tier=raw["tier"],
            question=raw["question"].strip(),
            tools=[
                [t] if isinstance(t, str) else [str(x) for x in t] for t in raw.get("tools", [])
            ],
            forbid=[str(t) for t in raw.get("forbid", [])],
            facts=[[str(alt) for alt in group] for group in raw.get("facts", [])],
            numbers=[Decimal(str(v)) for v in raw.get("numbers", [])],
            sources=[str(s) for s in raw.get("sources", [])],
            refuse=bool(raw.get("refuse", False)),
            chart=bool(raw.get("chart", False)),
            fake=list(raw.get("fake", [])),
        )
    except (TypeError, ArithmeticError) as exc:
        raise InvalidSuiteError(f"{where}: {exc}") from exc


# ------------------------------------------------------------------ one case


@dataclass
class CaseResult:
    id: str
    tier: str
    question: str
    status: str
    answer: str
    failures: list[str]
    tools_called: list[str]
    tool_calls: int
    iterations: int
    duration_ms: int
    tokens: int
    tools_ok: bool
    values_ok: bool | None  # None: the case expects no facts or numbers
    data_ok: bool | None  # expected numbers in some tool result; None: no numbers
    grounded: bool
    citations_ok: bool | None  # None: the case expects no documents
    refused: bool | None  # None: not a refusal case
    chart_ok: bool | None  # None: not a chart case
    json_in_answer: bool
    parsed_from_content: int  # tool calls written as text
    empty_replies: int
    trace_id: int | None = None
    judge_score: float | None = None
    judge_note: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}


def score_case(case: AgentCase, trace: AgentTrace) -> CaseResult:
    checks = trace.checks or {}
    called = trace.tools_used
    answer = trace.answer if trace.status != "llm_error" else ""
    failures: list[str] = []

    if trace.status != "answered":
        failures.append(f"status {trace.status}" + (f": {trace.error}" if trace.error else ""))

    tool_failures = [
        f"no {' or '.join(group)} call" for group in case.tools if not set(group) & set(called)
    ] + [f"called {name}" for name in case.forbid if name in called]
    failures += tool_failures

    missing = missing_facts(answer, case.facts)
    unstated = [n for n in case.numbers if not states_value(answer, n)]
    failures += [f"missing fact {' / '.join(group)}" for group in missing]
    failures += [f"missing number {n}" for n in unstated]
    values_ok = None if not (case.facts or case.numbers) else not (missing or unstated)
    data_ok = None if not case.numbers else all(tools_with_value(trace, n) for n in case.numbers)

    unsupported = list(checks.get("unsupported_numbers", []))
    if unsupported:
        failures.append(f"numbers from no tool: {', '.join(unsupported)}")
    citation_status = checks.get("citation_status")
    cited_files = list(dict.fromkeys(s["filename"] for s in checks.get("sources", [])))
    citation_failures: list[str] = []
    if citation_status == INVALID:
        citation_failures.append(f"cites passages never shown: {checks.get('cited')}")
    if case.sources:
        if citation_status == MISSING or not cited_files:
            citation_failures.append("cites no document")
        off_target = [f for f in cited_files if f not in case.sources]
        if off_target:
            citation_failures.append(f"cites {', '.join(off_target)}")
    failures += citation_failures
    grounded = bool(answer) and not unsupported and citation_status != INVALID
    citations_ok = None if not case.sources else not citation_failures

    refused = None
    if case.refuse:
        text = normalize(answer).replace("’", "'")
        refused = any(marker in text for marker in REFUSAL_MARKERS)
        if not refused:
            failures.append("did not refuse")

    chart_ok = None
    if case.chart:
        charts = checks.get("charts", [])
        chart_ok = bool(charts) and not any(c["unsupported_values"] for c in charts)
        if not charts:
            failures.append("no chart")
        elif not chart_ok:
            failures.append("chart values from no tool")

    json_in_answer = bool(_JSON_IN_ANSWER.search(answer))
    if json_in_answer:
        failures.append("JSON or code in the answer")

    llm_steps = [s for s in trace.steps if isinstance(s, LLMStep)]
    return CaseResult(
        id=case.id,
        tier=case.tier,
        question=case.question,
        status=trace.status,
        answer=trace.answer,
        failures=failures,
        tools_called=called,
        tool_calls=sum(isinstance(s, ToolStep) for s in trace.steps),
        iterations=trace.iterations,
        duration_ms=trace.duration_ms,
        tokens=trace.total_tokens,
        tools_ok=not tool_failures,
        values_ok=values_ok,
        data_ok=data_ok,
        grounded=grounded,
        citations_ok=citations_ok,
        refused=refused,
        chart_ok=chart_ok,
        json_in_answer=json_in_answer,
        parsed_from_content=sum(len(s.tool_calls) for s in llm_steps if s.parsed_from_content),
        empty_replies=sum(
            1 for s in llm_steps if s.tools_offered and not s.tool_calls and not s.content.strip()
        ),
        trace_id=trace.id,
    )


# ------------------------------------------------------------------ the totals


def _rate(values: Sequence[bool | None]) -> float | None:
    counted = [v for v in values if v is not None]
    return sum(counted) / len(counted) if counted else None


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    """The metrics of a run. Rates are over the cases a measure applies to."""
    seconds = [r.duration_ms / 1000 for r in results]
    judged = [r.judge_score for r in results if r.judge_score is not None]
    statuses: dict[str, int] = {}
    for r in results:
        statuses[r.status] = statuses.get(r.status, 0) + 1
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "pass_rate": _rate([r.passed for r in results]),
        "tool_selection": _rate([r.tools_ok for r in results]),
        "answer_values": _rate([r.values_ok for r in results]),
        "data_fetched": _rate([r.data_ok for r in results]),
        "grounded": _rate([r.grounded for r in results]),
        "citations_on_target": _rate([r.citations_ok for r in results]),
        "refusals": _rate([r.refused for r in results]),
        "charts": _rate([r.chart_ok for r in results]),
        "json_in_answer": _rate([r.json_in_answer for r in results]),
        "judge": statistics.fmean(judged) if judged else None,
        "mean_iterations": statistics.fmean(r.iterations for r in results) if results else None,
        "mean_tool_calls": statistics.fmean(r.tool_calls for r in results) if results else None,
        "tool_calls_as_text": sum(r.parsed_from_content for r in results),
        "empty_replies": sum(r.empty_replies for r in results),
        "mean_s": statistics.fmean(seconds) if seconds else None,
        "p50_s": statistics.median(seconds) if seconds else None,
        "max_s": max(seconds) if seconds else None,
        "mean_tokens": statistics.fmean(r.tokens for r in results) if results else None,
        "statuses": statuses,
        "tiers": {
            tier: {
                "cases": len(group),
                "passed": sum(r.passed for r in group),
            }
            for tier in TIERS
            if (group := [r for r in results if r.tier == tier])
        },
    }


# ------------------------------------------------------------------ the table

# (key, label, kind) in the order printed; kind: pct, num, int, s
METRIC_ROWS = (
    ("pass_rate", "Passed (all checks)", "pct"),
    ("tool_selection", "Tool selection", "pct"),
    ("answer_values", "Expected facts and numbers", "pct"),
    ("data_fetched", "Right data fetched", "pct"),
    ("grounded", "Grounded (no number from no tool)", "pct"),
    ("citations_on_target", "Citations on the right document", "pct"),
    ("refusals", "Refused when it should", "pct"),
    ("charts", "Charts drawn from tool data", "pct"),
    ("json_in_answer", "JSON or code in the answer", "pct"),
    ("judge", "LLM judge (faithfulness)", "num"),
    ("mean_iterations", "Model calls per question", "num"),
    ("mean_tool_calls", "Tool calls per question", "num"),
    ("tool_calls_as_text", "Tool calls written as text", "int"),
    ("empty_replies", "Empty replies", "int"),
    ("mean_s", "Seconds per question (mean)", "s"),
    ("p50_s", "Seconds per question (median)", "s"),
    ("max_s", "Seconds per question (max)", "s"),
    ("mean_tokens", "Tokens per question", "int"),
)


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value:.0%}"
    if kind == "num":
        return f"{value:.2f}"
    if kind == "s":
        return f"{value:.1f}"
    return f"{value:,.0f}"


def metrics_table(reports: Sequence[dict[str, Any]]) -> str:
    """A Markdown table of one or more reports' metrics, side by side, then the
    pass count per tier."""
    names = [report.get("label") or report["started_at"][:16] for report in reports]
    lines = [
        "| Metric | " + " | ".join(names) + " |",
        "|---|" + "---:|" * len(reports),
    ]
    for key, label, kind in METRIC_ROWS:
        values = [report["metrics"].get(key) for report in reports]
        if all(v is None for v in values):
            continue
        lines.append(f"| {label} | " + " | ".join(_fmt(v, kind) for v in values) + " |")
    for tier in TIERS:
        cells = []
        for report in reports:
            counts = report["metrics"]["tiers"].get(tier)
            cells.append(f"{counts['passed']}/{counts['cases']}" if counts else "-")
        if any(c != "-" for c in cells):
            lines.append(f"| Passed: {tier} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
