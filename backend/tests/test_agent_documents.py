"""`search_documents` and the hybrid run (PLAN_HYBRID.md Phase 4), on fakes.

The library holds the real Q3 sales meeting notes from the demo corpus; the business
database is the seeded one. The hybrid test is the CI version of the phase's "done
when": a scripted model calls both tools and cites the notes, and the answer check
traces 4,120 to both the sales report and the passage.
"""

import json
import shutil
from pathlib import Path

import pytest

from app import cli
from app.agent.business_seed import seed_business_db
from app.agent.loop import HYBRID_WHEN, run_agent, system_prompt
from app.agent.registry import ToolContext
from app.agent.tools import build_registry
from app.agent.tools.documents import NO_PASSAGES, SearchDocumentsArgs, search_documents
from app.agent.trace import ToolStep
from app.llm.base import ChatTurn, ToolCall
from app.retrieval.library import DocumentLibrary, SearchResult
from app.versioning import service
from app.versioning.agent_traces import get_trace, save_trace
from app.versioning.demo import seed_demo

DEMO = Path(__file__).parents[1] / "demo" / "hilda-luna"
NOTES = "07-q3-2026-sales-meeting-notes.txt"
HYBRID_QUESTION = "Do CH-2004's Q3 sales match what the Q3 meeting notes say?"


@pytest.fixture
def library(runtime, tmp_path) -> DocumentLibrary:
    """Two shelves: 'meetings' with the real Q3 notes, 'hr' with one short file."""
    root = tmp_path / "demo"
    (root / "meetings").mkdir(parents=True)
    (root / "hr").mkdir()
    shutil.copy(DEMO / "meetings" / NOTES, root / "meetings" / NOTES)
    (root / "hr" / "vacation.txt").write_text(
        "Employees get sixteen vacation days in their third year.", encoding="utf-8"
    )
    with runtime.session_factory() as db:
        seed_demo(
            db,
            settings=runtime.settings,
            embedding_client_for=runtime.embedding_client_for,
            vector_store=runtime.vector_store,
            root=root,
        )
    seed_business_db(runtime.settings.business_db_file)
    return DocumentLibrary.from_runtime(runtime)


@pytest.fixture
def ctx(runtime, library) -> ToolContext:
    return ToolContext(runtime.settings, documents=library).for_new_run()


def search(ctx: ToolContext, **arguments):
    return build_registry(ctx.documents).execute("search_documents", arguments, ctx)


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(name, arguments)


def turn(*calls: ToolCall, content: str = "") -> ChatTurn:
    return ChatTurn(content, list(calls), prompt_tokens=100, completion_tokens=10)


# ------------------------------------------------------------------ the tool


def test_passages_are_numbered_with_their_document(ctx):
    result = search(ctx, query="best-selling SKU CH-2004 units sold")
    assert result.ok, result.error
    passages = result.content["passages"]
    assert passages[0]["cite"] == "[1]" and passages[0]["document"] == NOTES
    assert set(passages[0]) <= {"cite", "document", "where", "text"}
    assert "4,120 units" in passages[0]["text"]
    assert [p["cite"] for p in passages] == [f"[{n}]" for n in range(1, len(passages) + 1)]


def test_the_trace_keeps_the_chunk_behind_each_number(ctx, runtime):
    result = search(ctx, query="CH-2004 units sold")
    meta = result.meta["passages"]
    assert [m["number"] for m in meta] == list(range(1, len(meta) + 1))
    assert {"chunk_id", "document_id", "version_id", "score", "page", "heading"} <= set(meta[0])
    assert "chunk_id" not in result.to_message()  # provenance only, never shown


def test_numbers_hold_for_the_whole_run(ctx):
    first = search(ctx, query="CH-2004 units sold", shelf="meetings")
    second = search(ctx, query="vacation days")
    numbers = {m["chunk_id"]: m["number"] for m in first.meta["passages"]}
    for passage in second.meta["passages"]:
        if passage["chunk_id"] in numbers:  # found again: same number
            assert passage["number"] == numbers[passage["chunk_id"]]
        else:  # new: numbered after everything shown before
            assert passage["number"] > max(numbers.values())


def test_a_new_run_numbers_from_one_again(ctx):
    search(ctx, query="CH-2004 units sold")
    fresh = ctx.for_new_run()
    assert search(fresh, query="CH-2004 units sold").content["passages"][0]["cite"] == "[1]"


@pytest.mark.parametrize("shelf", ["meetings", "Meetings", "meeting", " MEETINGS "])
def test_a_shelf_limits_the_search(ctx, shelf):
    result = search(ctx, query="sixteen vacation days units", shelf=shelf)
    assert result.content["shelves"] == ["meetings"]
    assert {p["document"] for p in result.content["passages"]} == {NOTES}


def test_an_unknown_shelf_searches_every_shelf_and_says_so(ctx):
    result = search(ctx, query="vacation days", shelf="policies")
    assert result.ok and result.content["shelves"] == ["hr", "meetings"]
    assert "No shelf is called 'policies'" in result.content["note"]


def test_shelves_hidden_from_the_portal_are_not_searched(ctx, runtime):
    with runtime.session_factory() as db:
        hr = service.get_active_collection(db, "hr")
        service.set_portal_visible(db, hr.id, False)
    result = search(ctx, query="sixteen vacation days")
    assert result.content["shelves"] == ["meetings"]
    assert "vacation.txt" not in result.to_message()


def test_no_match_says_so(runtime):
    class Empty:
        def search(self, query, shelf):
            return SearchResult(["hr"], [])

    ctx = ToolContext(runtime.settings, documents=Empty())
    result = search_documents(SearchDocumentsArgs(query="zzzz"), ctx)
    assert result.content == {
        "query": "zzzz",
        "shelves": ["hr"],
        "note": NO_PASSAGES,
        "passages": [],
    }


def test_without_a_library_the_tool_is_an_error_the_model_reads(runtime, library):
    registry = build_registry(library)
    result = registry.execute("search_documents", {"query": "x y"}, ToolContext(runtime.settings))
    assert not result.ok and "isn't available" in result.error


def test_an_empty_library_is_not_offered(runtime):
    library = DocumentLibrary.from_runtime(runtime)
    assert "search_documents" not in build_registry(library).names
    assert library.search("anything", None).note == "The document library is empty."


def test_the_tool_names_the_shelves(library):
    registry = build_registry(library)
    assert registry.names[0] == "search_documents"
    description = registry.get("search_documents").description
    assert description.endswith("Shelves: hr, meetings.")


def test_the_citation_rules_come_with_the_tool():
    import datetime as dt

    today = dt.date(2026, 11, 16)
    assert HYBRID_WHEN in system_prompt(today, documents=True)
    assert "passage number" not in system_prompt(today)


def test_luna_is_told_to_search_before_saying_the_documents_lack_it():
    """Run 1 of the agent eval: 6 of 7 document questions got "I don't have that"
    without a search (decision 0040)."""
    import datetime as dt

    prompt = system_prompt(dt.date(2026, 11, 16), documents=True)
    assert "call search_documents first" in prompt
    assert "Never say the documents don't have something" in prompt
    assert "search_documents first" not in system_prompt(dt.date(2026, 11, 16))


# ------------------------------------------------------------ the hybrid run


def hybrid_turns(answer: str) -> list[ChatTurn]:
    return [
        turn(
            call("search_documents", query="Q3 sales meeting CH-2004 units sold"),
            call(
                "sales_report",
                date_from="2026-07-01",
                date_to="2026-09-30",
                group_by="sku",
                top_n=3,
            ),
        ),
        turn(content=answer),
    ]


def run_hybrid(runtime, library, answer: str):
    runtime.fake_llm.turns = hybrid_turns(answer)
    return run_agent(
        HYBRID_QUESTION,
        llm=runtime.fake_llm,
        registry=build_registry(library),
        ctx=ToolContext(runtime.settings, documents=library),
        model="fake",
    )


def notes_number(trace) -> int:
    step = next(s for s in trace.steps if isinstance(s, ToolStep) and s.name == "search_documents")
    return next(m["number"] for m in step.meta["passages"] if m["filename"] == NOTES)


def test_the_hybrid_question_uses_both_tools_and_cites_the_notes(runtime, library):
    trace = run_hybrid(
        runtime,
        library,
        "Yes. The sales records show 4,120 units of CH-2004 in Q3 2026, and the meeting "
        "notes say 4,120 units were sold [1].",
    )
    n = notes_number(trace)
    assert n == 1
    assert trace.status == "answered"
    assert trace.tools_used == ["search_documents", "sales_report"]
    system = runtime.fake_llm.chat_calls[0]["messages"][0]["content"]
    assert "Cite each fact from a document" in system

    checks = trace.checks
    assert checks["citation_status"] == "ok" and checks["cited"] == [n]
    assert checks["sources"][0]["filename"] == NOTES
    claim = next(c for c in checks["numbers"] if c["text"] == "4,120")
    assert claim["sources"] == [f"search_documents [{n}]", "sales_report"]  # trace order
    assert checks["unsupported_numbers"] == []


def test_a_made_up_number_and_a_bad_citation_are_reported(runtime, library):
    trace = run_hybrid(runtime, library, "No: the database shows 3,900 units [42].")
    assert trace.checks["citation_status"] == "invalid"
    assert trace.checks["unsupported_numbers"] == ["3,900"]


def test_the_checks_are_stored_with_the_trace(runtime, library):
    trace = run_hybrid(runtime, library, "4,120 units.")
    with runtime.session_factory() as db:
        save_trace(db, trace)
        row = get_trace(db, trace.id)
        assert row.checks == trace.checks
        assert row.steps[1]["meta"]["passages"][0]["number"] == 1


def test_a_model_failure_has_no_checks(runtime, library):
    import httpx

    runtime.fake_llm.turns = [httpx.ConnectError("down")]
    trace = run_agent(
        "hola",
        llm=runtime.fake_llm,
        registry=build_registry(library),
        ctx=ToolContext(runtime.settings, documents=library),
    )
    assert trace.status == "llm_error" and trace.checks is None


# ---------------------------------------------------------------------- CLI


def test_cli_agent_searches_documents_and_prints_the_checks(runtime, library, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: runtime.settings)
    monkeypatch.setattr(cli, "build_runtime", lambda _settings: runtime)
    runtime.fake_llm.turns = hybrid_turns("The notes say 4,120 units [1]; the database 9,999.")
    assert cli.main(["agent", HYBRID_QUESTION]) == 0
    out = capsys.readouterr().out
    assert "tool  search_documents ok" in out
    assert f"Sources:\n[1] {NOTES}" in out
    assert "Citations: ok" in out
    assert "9,999          <- NOT FOUND IN ANY TOOL RESULT" in out
    with runtime.session_factory() as db:
        assert json.dumps(get_trace(db, 1).checks)  # stored
