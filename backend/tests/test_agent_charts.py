"""`make_chart`, its check and its endpoint (PLAN_HYBRID.md Phase 5, decision 0038).

A scripted model asks for monthly Q3 revenue, then charts it; the spec is stored in
the trace, served by the API, and every value in it is traced to the sales report.
"""

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app.agent.business_seed import seed_business_db
from app.agent.loop import CHART_RULE, run_agent, system_prompt
from app.agent.registry import ToolContext, ToolRegistry, parameters_schema
from app.agent.tools import build_registry
from app.agent.tools.charts import MakeChartArgs, chart_specs, chart_tools
from app.llm.base import ChatTurn, ToolCall
from app.main import create_app
from app.versioning.agent_traces import save_trace

LABELS = ["2026-07", "2026-08", "2026-09"]


def chart(ctx: ToolContext, **arguments):
    return ToolRegistry(chart_tools()).execute("make_chart", arguments, ctx)


@pytest.fixture
def ctx(runtime) -> ToolContext:
    return ToolContext(runtime.settings).for_new_run()


# ------------------------------------------------------------------ the tool


def test_the_schema_is_flat_with_series_as_an_object_of_lists():
    schema = parameters_schema(MakeChartArgs)
    assert schema["required"] == ["kind", "title", "labels", "series"]
    assert schema["properties"]["kind"]["enum"] == ["line", "bar", "pie"]
    assert schema["properties"]["series"]["additionalProperties"] == {
        "type": "array",
        "items": {"type": "number"},
    }


def test_a_chart_spec_has_one_row_per_label_and_one_key_per_series(ctx):
    result = chart(
        ctx,
        kind="bar",
        title="Q3 revenue by month",
        labels=LABELS,
        series={"Revenue MXN": [150000.5, 160000.0, 202725.01], "Units": [900, 950, 1000]},
        y_label="MXN",
    )

    assert result.ok
    assert result.content == {
        "chart_id": "chart-1",
        "kind": "bar",
        "title": "Q3 revenue by month",
        "points": 3,
        "series": ["Revenue MXN", "Units"],
        "note": result.content["note"],
    }
    spec = result.meta["chart"]
    assert spec["id"] == "chart-1"
    assert spec["series"] == ["Revenue MXN", "Units"]
    assert spec["data"][0] == {"label": "2026-07", "Revenue MXN": 150000.5, "Units": 900.0}
    assert "data" not in result.content  # the model never sees the spec again


def test_charts_are_numbered_per_run(ctx):
    first = chart(ctx, kind="line", title="A", labels=["x"], series={"s": [1]})
    second = chart(ctx, kind="line", title="B", labels=["x"], series={"s": [1]})
    again = chart(ctx, kind="line", title="A", labels=["x"], series={"s": [1]})
    assert [r.content["chart_id"] for r in (first, second, again)] == [
        "chart-1",
        "chart-2",
        "chart-1",
    ]
    fresh = chart(ctx.for_new_run(), kind="line", title="B", labels=["x"], series={"s": [1]})
    assert fresh.content["chart_id"] == "chart-1"


def test_a_small_models_json_strings_and_numeric_labels_are_accepted(ctx):
    result = chart(
        ctx,
        kind="line",
        title="By year",
        labels="[2025, 2026]",
        series='{"Revenue": [10, 20.5]}',
    )
    assert result.ok
    assert [row["label"] for row in result.meta["chart"]["data"]] == ["2025", "2026"]


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({"labels": ["a", "b"], "series": {"s": [1]}}, "1 values for 2 labels"),
        ({"labels": ["a"], "series": {}}, "at least one"),
        ({"kind": "pie", "labels": ["a"], "series": {"s": [1], "t": [2]}}, "exactly one"),
        ({"kind": "pie", "labels": ["a", "b"], "series": {"s": [1, -2]}}, "negative"),
        ({"kind": "radar"}, "kind"),
        ({"labels": [], "series": {"s": []}}, "labels"),
        ({"series": {"s": ["many"]}}, "series"),
    ],
)
def test_a_bad_shape_is_an_error_the_model_can_fix(ctx, arguments, problem):
    base = {"kind": "bar", "title": "T", "labels": ["a"], "series": {"s": [1]}}
    result = chart(ctx, **{**base, **arguments})
    assert not result.ok
    assert problem in result.error


def test_the_chart_rule_comes_with_the_tool():
    assert CHART_RULE in system_prompt(dt.date(2026, 11, 16), charts=True)
    assert "make_chart" in build_registry().names


# ------------------------------------------------- the run, the check, the API


@pytest.fixture
def seeded(runtime):
    seed_business_db(runtime.settings.business_db_file)
    return runtime


def chart_run(runtime, values: list[float]):
    runtime.fake_llm.turns = [
        ChatTurn(
            content="",
            tool_calls=[
                ToolCall(
                    "sales_report",
                    {"date_from": "2026-07-01", "date_to": "2026-09-30", "group_by": "month"},
                )
            ],
        ),
        ChatTurn(
            content="",
            tool_calls=[
                ToolCall(
                    "make_chart",
                    {
                        "kind": "line",
                        "title": "Q3 revenue by month",
                        "labels": LABELS,
                        "series": {"Revenue MXN": values},
                    },
                )
            ],
        ),
        ChatTurn(content="Here is Q3 revenue by month; the chart is below."),
    ]
    trace = run_agent(
        "Chart Q3 revenue by month",
        llm=runtime.fake_llm,
        registry=build_registry(),
        ctx=ToolContext(runtime.settings),
    )
    with runtime.session_factory() as db:
        save_trace(db, trace)
    return trace


def monthly_revenue(trace) -> list[float]:
    report = json.loads(trace.steps[1].result)
    return [row["revenue_mxn"] for row in report["rows"]]


def test_chart_values_from_the_sales_report_are_supported(seeded):
    runtime = seeded
    probe = chart_run(runtime, [1, 2, 3])
    values = monthly_revenue(probe)
    trace = chart_run(runtime, [round(v, 1) for v in values[:2]] + [values[2]])

    assert trace.status == "answered"
    assert trace.checks["charts"] == [
        {"chart_id": "chart-1", "values": 3, "unsupported_values": []}
    ]


def test_made_up_chart_values_are_reported(seeded):
    runtime = seeded
    trace = chart_run(runtime, [1.5, 999999.99, 3.25])
    (check,) = trace.checks["charts"]
    assert check["unsupported_values"] == [1.5, 999999.99, 3.25]


def test_the_api_serves_a_stored_chart(seeded):
    runtime = seeded
    trace = chart_run(runtime, [1, 2, 3])
    with TestClient(create_app(runtime, start_worker=False)) as client:
        ok = client.get(f"/api/agent/traces/{trace.id}/charts/chart-1")
        no_chart = client.get(f"/api/agent/traces/{trace.id}/charts/chart-9")
        no_trace = client.get("/api/agent/traces/9999/charts/chart-1")

    assert ok.status_code == 200
    assert ok.json() == {
        "id": "chart-1",
        "kind": "line",
        "title": "Q3 revenue by month",
        "y_label": None,
        "series": ["Revenue MXN"],
        "data": [
            {"label": "2026-07", "Revenue MXN": 1.0},
            {"label": "2026-08", "Revenue MXN": 2.0},
            {"label": "2026-09", "Revenue MXN": 3.0},
        ],
    }
    assert no_chart.status_code == 404
    assert no_trace.status_code == 404


def test_chart_specs_skip_failed_steps():
    steps = [
        {"kind": "tool", "ok": False, "meta": {"chart": {"id": "chart-1"}}},
        {"kind": "llm", "meta": {}},
        {"kind": "tool", "ok": True, "meta": {"chart": {"id": "chart-2", "kind": "bar"}}},
    ]
    assert list(chart_specs(steps)) == ["chart-2"]
