"""Checks an agent answer against its own trace (PLAN_HYBRID.md Phase 4).

Two checks, both read from the trace alone, so any stored run can be checked again:

- **Citations** (invariant 6). Every [n] must be a passage that `search_documents`
  showed in this run. The status uses the RAG app's words (`retrieval/citations.py`):
  `ok`, `missing` (passages were shown, none is cited), `invalid` (a number that was
  never shown) and `not_applicable` (no passages, no markers).
- **Numbers** (invariant 12). Each number in the answer is traced to what contains
  it: a tool result (`sales_report`), one passage (`search_documents [2]`), the
  question, or today's date. A number nothing contains is `unsupported`: the model
  made it up or did its own arithmetic. So the answer doesn't have to name its tools;
  the check names them (decision 0036).
- **Charts** (invariant 12, decision 0038). Every value in a `make_chart` chart must
  appear in a tool result shown before the chart was made; the ones that don't are
  listed per chart. A chart's own step is never evidence for anything.

The check reports and changes nothing. A small model writes numbers as "4,120",
"4.120" (Spanish), "519,725.51", "59,8 %"; each is read every way it can mean and
matches when one reading equals a tool's value at the precision the answer wrote
("519,726" matches 519725.51). SKUs (CH-2004), quarters (Q3), list markers and
citation markers are not numbers. ISO dates are compared as text.
"""

import datetime as dt
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.agent.trace import AgentTrace, ToolStep
from app.retrieval.citations import INVALID, MISSING, NOT_APPLICABLE, OK

DOCUMENTS_TOOL = "search_documents"
CHART_TOOL = "make_chart"

_CITATION = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
# A bracket of bigger numbers that aren't passages is data, e.g. a JSON array the model
# wrote ("[1200, 1500, 1420]"): its numbers are claims, not citations (decision 0039).
_MAX_CITATION_DIGITS = 2
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s+")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SEPARATOR = "[.,  ]"  # thousands or decimal; no-break spaces from "4 120"
# A number standing on its own: not part of a word ("Q3", "3rd") or a code ("CH-2004").
_NUMBER = re.compile(rf"(?<![\w.,])(?<!\w-)\d+(?:{_SEPARATOR}\d+)*(?!\w|-\w)(\s?%)?")
# In tool results every digit run counts, codes and dates included.
_ANY_NUMBER = re.compile(rf"\d+(?:{_SEPARATOR}\d+)*")
_DIGITS = re.compile(r"\d+")


@dataclass
class NumberClaim:
    text: str  # as written in the answer, e.g. "4,120" or "59.8%"
    sources: list[str] = field(default_factory=list)  # empty: unsupported


@dataclass
class AnswerCheck:
    citation_status: str
    cited: list[int]
    sources: list[dict[str, Any]]  # the cited passages: document, page, chunk
    numbers: list[NumberClaim]
    charts: list[dict[str, Any]] = field(default_factory=list)  # id, values, unsupported

    @property
    def unsupported(self) -> list[str]:
        return [claim.text for claim in self.numbers if not claim.sources]

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_status": self.citation_status,
            "cited": self.cited,
            "sources": self.sources,
            "numbers": [asdict(claim) for claim in self.numbers],
            "unsupported_numbers": self.unsupported,
            "charts": self.charts,
        }


def check_answer(trace: AgentTrace, today: dt.date | None = None) -> AnswerCheck:
    passages = shown_passages(trace)
    status, cited = _citations(trace.answer, passages)
    sources = [passages[n] for n in cited if n in passages]
    evidence = _evidence(trace, today or trace.started_at.date())
    numbers = [
        NumberClaim(text, [label for label, ev in evidence if ev.supports(text, values)])
        for text, values in _claims(trace.answer, passages)
    ]
    return AnswerCheck(status, cited, sources, numbers, _charts(trace))


def _charts(trace: AgentTrace) -> list[dict[str, Any]]:
    """Each chart made in the run, with the values no earlier tool result contains."""
    charts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, step in enumerate(trace.steps):
        chart = step.meta.get("chart") if isinstance(step, ToolStep) and step.ok else None
        if not chart or chart["id"] in seen:
            continue
        seen.add(chart["id"])
        evidence = [ev for _, ev in _evidence(trace, None, until=index)]
        values = [row[name] for row in chart["data"] for name in chart["series"]]
        unsupported = [
            value
            for value in values
            if not any(ev.supports(str(value), {_decimal(value)}) for ev in evidence)
        ]
        charts.append(
            {"chart_id": chart["id"], "values": len(values), "unsupported_values": unsupported}
        )
    return charts


def _decimal(value: float | int) -> Decimal:
    """A chart value as written: 519725.51 keeps two decimals, 4120.0 none."""
    number = Decimal(str(value))
    return number.to_integral_value() if number == number.to_integral_value() else number


def shown_passages(trace: AgentTrace) -> dict[int, dict[str, Any]]:
    """Every passage `search_documents` showed in the run, by its number."""
    passages: dict[int, dict[str, Any]] = {}
    for step in _document_steps(trace):
        for passage in step.meta.get("passages", []):
            passages.setdefault(int(passage["number"]), passage)
    return passages


def _document_steps(trace: AgentTrace) -> Iterator[ToolStep]:
    for step in trace.steps:
        if isinstance(step, ToolStep) and step.name == DOCUMENTS_TOOL and step.ok:
            yield step


def _citations(answer: str, passages: dict[int, dict[str, Any]]) -> tuple[str, list[int]]:
    cited = list(dict.fromkeys(_cited_numbers(answer, passages)))  # first-cited order, no repeats
    if not cited:
        return (MISSING if passages and answer.strip() else NOT_APPLICABLE), []
    if any(n not in passages for n in cited):
        return INVALID, cited
    return OK, cited


# ------------------------------------------------------------------ numbers


def _is_citation(group: str, passages: Mapping[int, Any]) -> bool:
    numbers = _DIGITS.findall(group)
    return all(int(n) in passages or len(n) <= _MAX_CITATION_DIGITS for n in numbers)


def _cited_numbers(answer: str, passages: Mapping[int, Any]) -> list[int]:
    return [
        int(n)
        for match in _CITATION.finditer(answer)
        if _is_citation(match.group(0), passages)
        for n in _DIGITS.findall(match.group(0))
    ]


def _claims(answer: str, passages: Mapping[int, Any]) -> list[tuple[str, set[Decimal]]]:
    """The numbers the answer states, each with every value it can mean. A date
    claim has no values: it's matched as text."""
    without_citations = _CITATION.sub(
        lambda m: " " if _is_citation(m.group(0), passages) else m.group(0), answer
    )
    text = _LIST_MARKER.sub("", without_citations)
    claims: list[tuple[str, set[Decimal]]] = []
    for date in _ISO_DATE.findall(text):
        claims.append((date, set()))
    text = _ISO_DATE.sub(" ", text)
    for match in _NUMBER.finditer(text):
        # A percentage matches the tools' percentages (share_of_revenue_pct: 59.8).
        token, percent = match.group(0), match.group(1) or ""
        claims.append((token.strip(), readings(token[: len(token) - len(percent)])))
    return claims


def readings(token: str) -> set[Decimal]:
    """Every value `token` can mean. "4,120" -> 4120; "4.120" -> 4120 or 4.12;
    "519.725,51" -> 519725.51; "59,8" -> 59.8."""
    parts = re.split(f"({_SEPARATOR})", token)
    groups, seps = parts[0::2], parts[1::2]
    if not seps:
        return {Decimal(token)}
    values: set[Decimal] = set()
    if len(set(seps)) == 1 and all(len(g) == 3 for g in groups[1:]):
        values.add(Decimal("".join(groups)))  # every separator groups thousands
    decimal_point = seps[-1] in ".,"
    if decimal_point and (
        len(seps) == 1
        or (
            len(set(seps[:-1])) == 1
            and seps[-2] != seps[-1]
            and all(len(g) == 3 for g in groups[1:-1])
        )
    ):
        values.add(Decimal("".join(groups[:-1]) + "." + groups[-1]))
    return values


@dataclass
class _Evidence:
    text: str  # matched for dates
    values: set[Decimal]

    def supports(self, claim: str, readings_: set[Decimal]) -> bool:
        if not readings_:  # an ISO date
            return claim in self.text
        return any(_rounds_to(value, reading) for reading in readings_ for value in self.values)


def _rounds_to(value: Decimal, reading: Decimal) -> bool:
    """`value` written with as many decimals as `reading` has gives `reading`."""
    exponent = reading.as_tuple().exponent
    places = Decimal(1).scaleb(exponent if isinstance(exponent, int) else 0)
    try:
        return value.quantize(places, rounding=ROUND_HALF_UP) == reading
    except InvalidOperation:
        return False


def _evidence(
    trace: AgentTrace, today: dt.date | None, *, until: int | None = None
) -> list[tuple[str, _Evidence]]:
    """(label, evidence) for everything a number may come from, in trace order:
    the question, the tool results (the steps before `until`, if given; never a
    chart's), and `today` when given."""
    evidence: list[tuple[str, _Evidence]] = [("question", _from_text(trace.question))]
    for step in trace.steps[:until]:
        if not isinstance(step, ToolStep) or not step.ok or step.name == CHART_TOOL:
            continue
        try:
            shown = json.loads(step.result)
        except ValueError:
            shown = step.result
        if step.name == DOCUMENTS_TOOL and isinstance(shown, dict):
            for passage in shown.get("passages", []):
                label = f"{DOCUMENTS_TOOL} {passage.get('cite', '')}".strip()
                evidence.append((label, _from_text(str(passage.get("text", "")))))
        else:
            evidence.append((step.name, _from_json(shown, step.result)))
    if today is not None:
        evidence.append(("today", _from_text(today.isoformat())))

    merged: dict[str, _Evidence] = {}  # the same tool called twice is one source
    for label, ev in evidence:
        if label in merged:
            merged[label].text += "\n" + ev.text
            merged[label].values |= ev.values
        else:
            merged[label] = ev
    return list(merged.items())


def _from_text(text: str) -> _Evidence:
    values: set[Decimal] = set()
    for token in _ANY_NUMBER.findall(text):
        values |= readings(token)
        values |= {Decimal(run) for run in _DIGITS.findall(token)}  # 2026 in 2026-07-01
    return _Evidence(text, values)


def _from_json(data: Any, raw: str) -> _Evidence:
    """Numbers in a tool's JSON: numeric values, numbers inside strings, and the
    length of every list (the model may count rows: "3 products")."""
    evidence = _Evidence(raw, set())

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, int | float):
            evidence.values.add(Decimal(str(node)))
        elif isinstance(node, str):
            evidence.values.update(_from_text(node).values)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            evidence.values.add(Decimal(len(node)))
            for item in node:
                walk(item)

    walk(data)
    return evidence
