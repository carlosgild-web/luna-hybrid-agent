"""`make_chart`: a chart spec the chat renders (PLAN_HYBRID.md Phase 5, decision 0038).

The model passes the chart's kind, title, labels and one or more named series of
values; the tool checks the shape and builds a spec in the form recharts reads (one
object per label, one key per series). The spec is kept in the step's `meta`, so it
is stored with the trace and served from it by `GET /api/agent/traces/{id}/charts/{chart_id}`;
the model only sees the chart's id and size. The answer check (`agent/answer.py`)
traces every value in a chart to an earlier tool result, as it does for the answer's
numbers (invariant 12).

Arguments stay flat (the registry has no nested models): `series` is an object of
name -> list of numbers. Small models sometimes send a list or an object as a JSON
string, or labels as numbers; both are accepted.
"""

import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.agent.registry import Tool, ToolArgs, ToolContext, ToolOutput

MAX_LABELS = 60
MAX_SERIES = 6

SHOWN_NOTE = (
    "The chart is shown to the user under your answer. Mention it in one sentence; "
    "don't list every value again."
)


def chart_id(number: int) -> str:
    return f"chart-{number}"


class MakeChartArgs(ToolArgs):
    kind: Literal["line", "bar", "pie"] = Field(
        description="line for a trend over time, bar to compare groups, pie for shares of a total"
    )
    title: str = Field(min_length=1, max_length=120, description="A short title")
    labels: list[str] = Field(
        min_length=1,
        max_length=MAX_LABELS,
        description="The x-axis labels (pie: the slices), e.g. months or SKUs",
    )
    series: dict[str, list[float]] = Field(
        description="Each series' name and its values, one per label, copied from a tool "
        'result, e.g. {"Revenue MXN": [120500.25, 98000.0]}. A pie takes one series.',
    )
    y_label: str | None = Field(None, max_length=40, description="The unit, e.g. MXN or units")

    @field_validator("labels", "series", mode="before")
    @classmethod
    def _lenient(cls, value: Any, info) -> Any:
        if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
            try:
                value = json.loads(value)
            except ValueError:
                return value
        if info.field_name == "labels" and isinstance(value, list):
            return [str(item) if isinstance(item, int | float) else item for item in value]
        return value

    @model_validator(mode="after")
    def _shape(self) -> "MakeChartArgs":
        if not self.series:
            raise ValueError("series needs at least one named list of values")
        if len(self.series) > MAX_SERIES:
            raise ValueError(f"at most {MAX_SERIES} series")
        for name, values in self.series.items():
            if not name.strip():
                raise ValueError("every series needs a name")
            if len(values) != len(self.labels):
                raise ValueError(
                    f"series {name!r} has {len(values)} values for {len(self.labels)} labels; "
                    "give one value per label"
                )
        if self.kind == "pie":
            if len(self.series) != 1:
                raise ValueError("a pie chart takes exactly one series")
            if any(v < 0 for values in self.series.values() for v in values):
                raise ValueError("a pie chart can't show negative values")
        return self


def make_chart(args: MakeChartArgs, ctx: ToolContext) -> ToolOutput:
    names = list(args.series)
    data = [
        {"label": label, **{name: args.series[name][i] for name in names}}
        for i, label in enumerate(args.labels)
    ]
    spec: dict[str, Any] = {
        "kind": args.kind,
        "title": args.title,
        "y_label": args.y_label,
        "series": names,
        "data": data,
    }
    key = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    spec = {"id": chart_id(ctx.charts.number(key)), **spec}
    content = {
        "chart_id": spec["id"],
        "kind": args.kind,
        "title": args.title,
        "points": len(data),
        "series": names,
        "note": SHOWN_NOTE,
    }
    return ToolOutput(content, meta={"chart": spec})


DESCRIPTION = (
    "Draw a chart for the user from numbers you already have from another tool: a line "
    "for a trend over time, bars to compare groups, a pie for shares of a total. Use it "
    "only when the user asks for a chart or a graph. Copy the values exactly from the "
    "tool results."
)


def chart_tools() -> list[Tool]:
    return [
        Tool(name="make_chart", description=DESCRIPTION, args=MakeChartArgs, handler=make_chart)
    ]


def chart_specs(steps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The charts in a stored trace's steps, by id (a reused step repeats one)."""
    specs: dict[str, dict[str, Any]] = {}
    for step in steps:
        chart = (step.get("meta") or {}).get("chart")
        if step.get("kind") == "tool" and step.get("ok") and chart:
            specs.setdefault(chart["id"], chart)
    return specs
