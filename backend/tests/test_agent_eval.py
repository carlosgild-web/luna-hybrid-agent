"""The agent evaluation (PLAN_HYBRID.md Phase 7, decision 0040), on fakes.

The suite's own run is the CI version of the phase's "done when": every case's
scripted run, against the real tools, the seeded business database and the demo
documents, passes all its checks. The other tests break a run on purpose and
check that the score names what broke.
"""

import dataclasses
import json
import re
from decimal import Decimal

import pytest

from app import cli
from app.agent.answer import states_value
from app.agent.loop import AgentLimits, run_agent
from app.agent.trace import AgentTrace, ToolStep
from app.eval import agent_runner
from app.eval.agent_suite import (
    TIERS,
    AgentCase,
    InvalidSuiteError,
    load_suite,
    metrics_table,
    score_case,
    summarize,
)

SUITE = load_suite()
CASES = {case.id: case for case in SUITE.cases}


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    with agent_runner.fake_runtime(tmp_path_factory.mktemp("agent-eval") / "data") as rt:
        yield rt


def run(runtime, cases: list[AgentCase], **kwargs):
    return agent_runner.run_suite(
        SUITE,
        cases,
        runtime=runtime,
        model="scripted",
        llm_for=agent_runner.ScriptedLLM,
        limits=AgentLimits(),
        **kwargs,
    )


def variant(case_id: str, *fake, **changes) -> AgentCase:
    """A suite case with another scripted run (and other changes)."""
    return dataclasses.replace(CASES[case_id], fake=list(fake), **changes)


def failures(runtime, case: AgentCase) -> list[str]:
    return run(runtime, [case])[0].failures


# ------------------------------------------------------------------ the suite


def test_the_suite_has_thirty_cases_in_every_tier():
    assert len(SUITE.cases) == 30
    assert {case.tier for case in SUITE.cases} == set(TIERS)
    assert all(case.fake for case in SUITE.cases), "every case needs a scripted run"
    assert SUITE.today.isoformat() == "2026-11-16"


def test_every_scripted_run_passes_against_the_real_tools(runtime):
    results = run(runtime, SUITE.cases)
    broken = {r.id: r.failures for r in results if not r.passed}
    assert broken == {}
    metrics = summarize(results)
    assert metrics["pass_rate"] == 1.0
    assert metrics["data_fetched"] == 1.0
    assert metrics["tiers"]["hybrid"] == {"cases": 6, "passed": 6}


def test_the_hybrid_case_is_the_meeting_notes_figure(runtime):
    (result,) = run(runtime, [CASES["hyb-ch2004"]])
    assert result.tools_called == ["search_documents", "sales_report"]
    assert result.citations_ok and result.data_ok


# ------------------------------------------------------------------ failures


def test_a_forbidden_tool_fails_tool_selection(runtime):
    case = variant(
        "doc-pto-years",
        ["sql_select", {"query": "SELECT COUNT(*) FROM empleados"}],
        ["search_documents", {"query": "PTO days per year years of service", "shelf": "hr"}],
        "Employees get 16 PTO days in years 3 to 5 "
        "{cite:Hilda & Luna Beadworks - Sample HR Policy.docx}.",
    )
    assert failures(runtime, case) == ["called sql_select"]


def test_a_missing_tool_fails_tool_selection(runtime):
    case = variant("hyb-ch2004", "CH-2004 sold 4,120 units in Q3.")
    (result,) = run(runtime, [case])
    assert not result.tools_ok
    assert "no search_documents call" in result.failures
    assert "no sales_report or sql_select call" in result.failures
    assert "numbers from no tool: 4,120" in result.failures


def test_a_made_up_number_is_ungrounded(runtime):
    case = variant(
        "db-stock-es",
        ["check_inventory", {"sku": "CH-2004"}],
        "Hay 2,100 piezas de CH-2004.",
    )
    (result,) = run(runtime, [case])
    assert result.failures == ["missing number 1900", "numbers from no tool: 2,100"]
    assert result.data_ok is True  # the tool had the right number; the answer didn't
    assert not result.grounded


def test_the_wrong_period_fails_the_data_check(runtime):
    case = variant(
        "db-top5-q3",
        [
            "sales_report",
            {"date_from": "2026-04-01", "date_to": "2026-06-30", "group_by": "sku", "top_n": 5},
        ],
        "The top SKUs were CH-2004, GB-1014, GB-1001, GB-1002 and CH-2033.",
    )
    (result,) = run(runtime, [case])
    assert result.data_ok is False
    assert "missing number 4120" in result.failures


def test_a_refusal_case_that_answers_fails(runtime):
    case = variant("ref-delete", "Done, the orders table is gone.")
    assert failures(runtime, case) == ["did not refuse"]


def test_a_refusal_with_an_invented_figure_fails(runtime):
    case = variant("ref-q1-2027", "There is no data yet, but expect about 600,000 MXN.")
    assert failures(runtime, case) == ["numbers from no tool: 600,000"]


def test_a_chart_written_as_json_fails(runtime):
    case = variant(
        "db-chart",
        ["sales_report", {"date_from": "2026-07-01", "date_to": "2026-09-30", "group_by": "month"}],
        '```json\n{"labels": ["2026-07"], "series": {"revenue_mxn": [225261.38]}}\n```',
    )
    (result,) = run(runtime, [case])
    assert result.json_in_answer and result.chart_ok is False
    assert result.failures == ["no make_chart call", "no chart", "JSON or code in the answer"]


def test_a_chart_with_values_from_no_tool_fails(runtime):
    case = variant(
        "db-chart",
        ["sales_report", {"date_from": "2026-07-01", "date_to": "2026-09-30", "group_by": "month"}],
        [
            "make_chart",
            {"kind": "bar", "title": "Q3", "labels": ["Jul"], "series": {"mxn": [999999.0]}},
        ],
        "Here is the chart.",
    )
    assert failures(runtime, case) == ["chart values from no tool"]


def test_citing_another_document_fails(runtime):
    case = variant(
        "doc-carrier",
        ["search_documents", {"query": "carrier shipping United States Canada DHL"}],
        ["search_documents", {"query": "gloves resin nitrile", "shelf": "hr"}],
        "DHL ships them {cite:04-workshop-safety-training.docx}.",
    )
    assert failures(runtime, case) == ["cites 04-workshop-safety-training.docx"]


def test_a_document_answer_without_citations_fails(runtime):
    case = variant(
        "doc-carrier",
        [
            "search_documents",
            {"query": "carrier shipping United States Canada", "shelf": "operations"},
        ],
        "DHL ships them.",
    )
    assert failures(runtime, case) == ["cites no document"]


def test_a_run_that_hits_the_limit_fails(runtime):
    call = ["check_inventory", {"sku": "CH-2004"}]
    case = variant("db-stock-es", call, call, call, "Hay 1,900 piezas.")
    (result,) = agent_runner.run_suite(
        SUITE,
        [case],
        runtime=runtime,
        model="scripted",
        llm_for=agent_runner.ScriptedLLM,
        limits=AgentLimits(max_iterations=3),
    )
    assert result.status == "max_iterations"
    # The last call offers no tools, so the scripted fourth call has no answer to show.
    assert result.failures == ["status max_iterations", "missing number 1900"]


def test_a_broken_script_fails_the_case_instead_of_the_run(runtime):
    case = variant(
        "doc-carrier",
        ["search_documents", {"query": "carrier shipping", "shelf": "operations"}],
        "DHL {cite:no-such-file.md}.",
    )
    (result,) = run(runtime, [case])
    assert result.status == "llm_error"
    assert "no-such-file.md" in result.failures[0]


# ------------------------------------------------------------------ numbers


@pytest.mark.parametrize(
    ("answer", "value"),
    [
        ("519,725.51 MXN", "519725.51"),
        ("519.725,51 MXN", "519725.51"),
        ("about 519,726 MXN", "519725.51"),
        ("60% of revenue", "59.8"),
        ("59,8 %", "59.8"),
        ("4.120 unidades", "4120"),
        ("$1.60", "1.60"),
    ],
)
def test_a_value_is_stated_at_any_precision(answer, value):
    assert states_value(answer, Decimal(value))


@pytest.mark.parametrize(
    ("answer", "value"),
    [("4,000 units", "4120"), ("CH-2004", "2004"), ("Q3", "3"), ("512,800", "519725.51")],
)
def test_other_numbers_dont_state_the_value(answer, value):
    assert not states_value(answer, Decimal(value))


# ------------------------------------------------------------------ the file


def write(tmp_path, text: str):
    path = tmp_path / "suite.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("cases: []\ntoday: soon\n", "'today' must be a date"),
        ("- id: a\n", "a 'cases' list"),
        ("cases:\n  - {id: a, tier: db, question: q, tool: [x]}\n", "unknown keys ['tool']"),
        ("cases:\n  - {id: a, tier: sql, question: q}\n", "tier must be one of"),
        ("cases:\n  - {id: a, tier: db}\n", "'question' is required"),
        (
            "cases:\n  - {id: a, tier: db, question: q}\n  - {id: a, tier: db, question: r}\n",
            "duplicate case ids: a",
        ),
        ("cases:\n  - {id: a, tier: db, question: q, numbers: [lots]}\n", "case 'a'"),
    ],
)
def test_a_bad_suite_is_rejected(tmp_path, text, message):
    with pytest.raises(InvalidSuiteError, match=re.escape(message)):
        load_suite(write(tmp_path, text))


# ------------------------------------------------------------------ judge, report, CLI


def test_the_judge_reads_the_tool_results():
    trace = AgentTrace(question="q", model="m", answer="CH-2004 sold 4,120 units.")
    trace.steps.append(ToolStep(1, "sales_report", {}, True, '{"units": 4120}', 5))
    prompt = agent_runner.judge_prompt(trace)
    assert '[1] sales_report: {"units": 4120}' in prompt
    assert prompt.endswith("CH-2004 sold 4,120 units.\n\nVerdict:")


def test_a_failing_judge_leaves_the_score_empty():
    class Broken(agent_runner.ScriptedLLM):
        def generate(self, prompt, **kwargs):
            raise TimeoutError("slow")

    trace = AgentTrace(question="q", model="m", answer="a")
    score, note = agent_runner.judge_answer(Broken(CASES["ref-ceo"]), trace, AgentLimits())
    assert score is None and "TimeoutError" in note


def test_the_judge_score_is_averaged(runtime):
    results = run(runtime, [CASES["db-stock-es"]], judge=agent_runner.ScriptedLLM(CASES["ref-ceo"]))
    assert results[0].judge_score == 1.0
    assert summarize(results)["judge"] == 1.0


def test_rates_count_only_the_cases_they_apply_to(runtime):
    results = run(runtime, [CASES["db-stock-es"], CASES["ref-delete"]])
    metrics = summarize(results)
    assert metrics["refusals"] == 1.0  # one refusal case, not one of two
    assert metrics["citations_on_target"] is None  # no case expects documents
    assert metrics["statuses"] == {"answered": 2}


def report(results, label):
    return agent_runner.build_report(
        results,
        label=label,
        model="scripted",
        limits=AgentLimits(),
        started_at=agent_runner.now(),
        fake=True,
        judge_model=None,
    )


def test_two_reports_print_side_by_side(runtime):
    good = run(runtime, [CASES["db-stock-es"]])
    bad = run(runtime, [variant("db-stock-es", "Hay 2,100 piezas.")])
    table = metrics_table([report(good, "before"), report(bad, "after")])
    assert table.splitlines()[0] == "| Metric | before | after |"
    assert "| Passed (all checks) | 100% | 0% |" in table
    assert "| Passed: db | 1/1 | 0/1 |" in table
    assert "refusal" not in table  # no tier without cases


def test_a_report_round_trips_through_its_file(runtime, tmp_path):
    written = agent_runner.write_report(
        report(run(runtime, [CASES["ext-rate"]]), "Run 1"), tmp_path
    )
    assert written.name.endswith("-run-1.json")
    loaded = agent_runner.load_report(written)
    assert loaded["config"]["external_apis"] is False
    assert loaded["cases"][0]["passed"] is True


def test_the_cli_runs_the_fake_suite_and_compares_reports(tmp_path, capsys):
    out = tmp_path / "reports"
    assert cli.main(["agent-eval", "--fake", "--tier", "external", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "[ 3/3] pass  ext-convert-es" in printed
    assert "| Passed: external | 3/3 |" in printed
    (path,) = out.glob("*.json")
    assert json.loads(path.read_text(encoding="utf-8"))["metrics"]["passed"] == 3

    assert cli.main(["agent-eval-compare", str(path), str(path)]) == 0
    compared = capsys.readouterr().out
    assert compared.startswith("| Metric | fake | fake |")
    assert "fixed" not in compared and "broken" not in compared  # nothing changed


def test_the_cli_fails_when_a_fake_case_fails(tmp_path, capsys):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        "cases:\n  - id: bad\n    tier: db\n    question: q\n    numbers: [7]\n"
        "    fake: ['The answer is 8.']\n",
        encoding="utf-8",
    )
    assert cli.main(["agent-eval", "--fake", "--suite", str(suite)]) == 1
    assert "FAIL  bad" in capsys.readouterr().out


def test_run_agent_is_what_the_suite_runs(runtime):
    """The suite adds nothing to the loop: the same question, run by hand, scores the same."""
    case = CASES["db-restock"]
    from app.agent.registry import ToolContext
    from app.agent.tools import build_registry
    from app.retrieval.library import DocumentLibrary

    documents = DocumentLibrary.from_runtime(runtime)
    trace = run_agent(
        case.question,
        llm=agent_runner.ScriptedLLM(case),
        registry=build_registry(documents),
        ctx=ToolContext(runtime.settings, documents=documents),
        today=SUITE.today,
    )
    assert score_case(case, trace).passed


# ------------------------------------------------------------------ fixes from the real runs


def test_a_citation_on_a_database_fact_is_removed_by_the_loop(runtime):
    """Run 2's new worst failure: "[1]" after database facts, with no search made."""
    case = variant(
        "db-wholesale-share",
        [
            "sales_report",
            {"date_from": "2026-07-01", "date_to": "2026-09-30", "group_by": "channel"},
        ],
        "59.8% of Q3 2026 revenue came from wholesale. [1]",
    )
    traces = []
    (result,) = run(runtime, [case], save=traces.append)
    assert result.passed, result.failures
    assert traces[0].answer == "59.8% of Q3 2026 revenue came from wholesale."
    assert traces[0].checks["removed_citations"] == ["[1]"]
    llm_text = [s.content for s in traces[0].steps if not isinstance(s, ToolStep)][-1]
    assert llm_text.endswith("[1]")  # the model's own text stays in the trace


def test_a_stored_trace_loads_back_as_the_same_run(runtime):
    from app.versioning.agent_traces import load_trace, save_trace

    saved = []

    def save(trace):
        with runtime.session_factory() as db:
            save_trace(db, trace)
        saved.append(trace)

    (result,) = run(runtime, [CASES["hyb-ch2004"]], save=save)
    with runtime.session_factory() as db:
        loaded = load_trace(db, result.trace_id)
    assert loaded.steps_as_dicts() == saved[0].steps_as_dicts()
    assert score_case(CASES["hyb-ch2004"], loaded).to_dict() == result.to_dict()


def test_rescore_scores_the_stored_traces_with_the_current_suite(runtime, tmp_path, monkeypatch):
    from app.versioning.agent_traces import save_trace

    def save(trace):
        with runtime.session_factory() as db:
            save_trace(db, trace)

    wrong_source = variant(  # a right answer from a file the old case didn't list
        "doc-returns",
        [
            "search_documents",
            {"query": "return policy unused beads 30 days", "shelf": "customer-support"},
        ],
        "Unused beads can be returned within 30 days {cite:08-customer-faq.txt}.",
        sources=["09-shipping-returns-policy.md"],
    )
    results = run(runtime, [wrong_source], save=save)
    assert results[0].failures == ["cites 08-customer-faq.txt"]
    path = agent_runner.write_report(report(results, "old"), tmp_path)

    monkeypatch.setattr(cli, "build_runtime", lambda _settings: runtime)
    assert cli.main(["agent-eval-rescore", str(path), "--out", str(tmp_path / "new")]) == 0
    (new,) = (tmp_path / "new").glob("*.json")
    rescored = agent_runner.load_report(new)
    assert rescored["label"] == "old (rescored)"
    assert rescored["cases"][0]["passed"] is True  # the suite now lists the FAQ
    assert rescored["config"]["rescored_from"] == path.name
