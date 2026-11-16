"""The answer check (decision 0036): citations against the passages the run showed,
and every number in the answer against the tool results that contain it."""

import datetime as dt
import json
from decimal import Decimal

import pytest

from app.agent.answer import check_answer, readings, shown_passages
from app.agent.trace import AgentTrace, ToolStep

TODAY = dt.date(2026, 11, 16)

INVENTORY = {
    "count": 3,
    "items": [
        {"sku": "GB-1014", "stock": 410, "reorder_point": 500},
        {"sku": "CH-2004", "stock": 1900, "reorder_point": 2000},
        {"sku": "BN-9001", "stock": 12, "reorder_point": 20},
    ],
}
SALES = {
    "date_from": "2026-07-01",
    "date_to": "2026-09-30",
    "groups": [{"group": "wholesale", "mxn": 310_795.86, "share_of_revenue_pct": 59.8}],
    "total_mxn": 519_725.51,
}
NOTES = "Best-selling SKU: CH-2004 'Crescent Moon & Star' charm — 4,120 units sold"


def tool(name: str, content: dict, *, meta: dict | None = None, ok: bool = True) -> ToolStep:
    result = json.dumps(content, ensure_ascii=False) if ok else '{"error":"bad"}'
    return ToolStep(1, name, {}, ok, result, 5, meta=meta or {})


def documents(*passages: tuple[int, str]) -> ToolStep:
    shown = [{"cite": f"[{n}]", "document": f"doc{n}.txt", "text": text} for n, text in passages]
    meta = [
        {"number": n, "chunk_id": 100 + n, "filename": f"doc{n}.txt", "page": None, "heading": None}
        for n, _ in passages
    ]
    return tool("search_documents", {"passages": shown}, meta={"passages": meta})


def check(answer: str, *steps: ToolStep, question: str = "?"):
    trace = AgentTrace(question=question, model="fake", steps=list(steps), answer=answer)
    return check_answer(trace, TODAY)


def sources_of(result, text: str) -> list[str]:
    return next(claim.sources for claim in result.numbers if claim.text == text)


# ---------------------------------------------------------------- readings


@pytest.mark.parametrize(
    ("token", "values"),
    [
        ("12", {"12"}),
        ("4,120", {"4120", "4.120"}),
        ("4.120", {"4120", "4.120"}),  # Spanish thousands, or a decimal
        ("1,234,567", {"1234567"}),
        ("519,725.51", {"519725.51"}),
        ("519.725,51", {"519725.51"}),
        ("59,8", {"59.8"}),
        ("4 120", {"4120"}),  # a narrow no-break space groups thousands
    ],
)
def test_a_number_is_read_every_way_it_can_mean(token, values):
    assert readings(token) == {Decimal(v) for v in values}


# ----------------------------------------------------------------- numbers


def test_numbers_are_traced_to_the_tool_that_returned_them():
    answer = "Q3 revenue was 519,725.51 MXN; wholesale was 59.8%."
    result = check(answer, tool("sales_report", SALES))
    assert sources_of(result, "519,725.51") == ["sales_report"]
    assert sources_of(result, "59.8%") == ["sales_report"]
    assert result.unsupported == []


def test_a_number_from_nowhere_is_unsupported():
    """The Phase 3 failure: a model said "100% wholesale" after grouping by quarter."""
    result = check("Wholesale was 100% of Q3 revenue.", tool("sales_report", SALES))
    assert result.unsupported == ["100%"]


def test_the_model_may_round_but_not_change_a_number():
    sales = tool("sales_report", SALES)
    assert check("About 519,726 MXN.", sales).unsupported == []
    assert check("About 59,8 %.", sales).unsupported == []
    assert check("About 520,000 MXN.", sales).unsupported == ["520,000"]


def test_spanish_thousands_match():
    result = check("CH-2004 tiene 1.900 unidades.", tool("check_inventory", INVENTORY))
    assert result.unsupported == []
    assert sources_of(result, "1.900") == ["check_inventory"]


def test_skus_quarters_list_markers_and_citations_are_not_numbers():
    answer = (
        "Need restocking in Q3:\n"
        "1. GB-1014 (410 units) [1]\n"
        "2. CH-2004 (1,900 units)\n"
        "3. BN-9001 (12 units)"
    )
    result = check(answer, tool("check_inventory", INVENTORY))
    assert [claim.text for claim in result.numbers] == ["410", "1,900", "12"]
    assert result.unsupported == []


def test_counting_rows_is_supported():
    result = check("3 products need restocking.", tool("check_inventory", {"items": [1, 2, 3]}))
    assert result.unsupported == []


def test_dates_are_compared_as_text():
    sales = tool("sales_report", SALES)
    assert check("From 2026-07-01 to 2026-09-30.", sales).unsupported == []
    assert check("Since 2026-06-30.", sales).unsupported == ["2026-06-30"]


def test_the_question_and_today_are_sources_too():
    result = check("As of 2026-11-16, Q3 2026 is closed.", question="How did Q3 2026 go?")
    assert sources_of(result, "2026-11-16") == ["today"]
    assert sources_of(result, "2026") == ["question", "today"]


def test_a_number_in_a_passage_names_the_passage():
    result = check(
        "The database and the notes both say 4,120 units [1].",
        tool("sales_report", {"groups": [{"sku": "CH-2004", "units": 4120}]}),
        documents((1, NOTES), (2, "Other text, 37 units.")),
    )
    assert sources_of(result, "4,120") == ["sales_report", "search_documents [1]"]


def test_a_failed_tool_supports_nothing():
    result = check("Stock is 1,900.", tool("check_inventory", INVENTORY, ok=False))
    assert result.unsupported == ["1,900"]


# --------------------------------------------------------------- citations


def test_citations_point_at_shown_passages():
    shown = documents((1, NOTES), (2, "Returns within 30 days."))
    result = check("Returns are accepted within 30 days [2].", shown)
    assert (result.citation_status, result.cited) == ("ok", [2])
    assert [source["chunk_id"] for source in result.sources] == [102]


def test_a_citation_to_a_passage_never_shown_is_invalid():
    result = check("It sold 4,120 units [3].", documents((1, NOTES)))
    assert result.citation_status == "invalid"


def test_passages_shown_but_none_cited_is_missing():
    assert check("It sold 4,120 units.", documents((1, NOTES))).citation_status == "missing"


def test_no_documents_no_citations_is_not_applicable():
    result = check("Stock is 1,900.", tool("check_inventory", INVENTORY))
    assert result.citation_status == "not_applicable"


def test_passages_keep_their_first_metadata_across_searches():
    trace = AgentTrace(
        question="?",
        model="fake",
        steps=[documents((1, NOTES)), documents((1, NOTES), (2, "More."))],
    )
    assert sorted(shown_passages(trace)) == [1, 2]


def test_to_dict_lists_the_unsupported_numbers():
    data = check("Stock is 7.", tool("check_inventory", INVENTORY)).to_dict()
    assert data["unsupported_numbers"] == ["7"]
    assert data["numbers"] == [{"text": "7", "sources": []}]
