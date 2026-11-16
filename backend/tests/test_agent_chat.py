"""The agent in the chat (PLAN_HYBRID.md Phase 6, decision 0039), on fakes.

A scripted model reads the meeting notes and the sales report, then charts Q3; the
chat streams each step as it ends, saves the answer with a link to its trace, and the
trace endpoint returns the whole run: the CI version of the phase's "done when".
"""

import json
import queue
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.business_seed import seed_business_db
from app.api.routes.agent import _run_and_save
from app.db.models import AgentTraceRecord, Message, MessageSource
from app.llm.base import ChatTurn, ToolCall
from app.main import create_app
from app.versioning import history
from app.versioning.demo import seed_demo

DEMO = Path(__file__).parents[1] / "demo" / "hilda-luna"
NOTES = "07-q3-2026-sales-meeting-notes.txt"
QUESTION = "Do CH-2004's Q3 sales match the meeting notes? Chart them by month."
Q3 = {"date_from": "2026-07-01", "date_to": "2026-09-30"}


@pytest.fixture
def seeded(runtime, tmp_path):
    root = tmp_path / "demo" / "meetings"
    root.mkdir(parents=True)
    shutil.copy(DEMO / "meetings" / NOTES, root / NOTES)
    with runtime.session_factory() as db:
        seed_demo(
            db,
            settings=runtime.settings,
            embedding_client_for=runtime.embedding_client_for,
            vector_store=runtime.vector_store,
            root=root.parent,
        )
    seed_business_db(runtime.settings.business_db_file)
    return runtime


@pytest.fixture
def client(seeded):
    with TestClient(create_app(seeded, start_worker=False)) as c:
        yield c


def turn(*calls: ToolCall, content: str = "") -> ChatTurn:
    return ChatTurn(content, list(calls), prompt_tokens=100, completion_tokens=10)


def script_hybrid_run(runtime) -> None:
    runtime.fake_llm.turns = [
        turn(
            ToolCall("search_documents", {"query": "Q3 sales meeting CH-2004 units"}),
            ToolCall("sales_report", {**Q3, "group_by": "sku", "top_n": 5}),
        ),
        turn(
            ToolCall(
                "make_chart",
                {
                    "kind": "bar",
                    "title": "Q3 units",
                    "labels": ["2026-07", "2026-08"],
                    "series": {"Units": [1, 2]},
                },
            )
        ),
        turn(content="The notes [1] and the sales records agree. The chart is below."),
    ]


def events(response) -> list[tuple[str, dict]]:
    out = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_a_hybrid_run_streams_its_steps_then_the_answer(client, seeded):
    script_hybrid_run(seeded)
    response = client.post("/api/agent/chat", json={"question": QUESTION})
    assert response.status_code == 200
    stream = events(response)

    assert [name for name, _ in stream] == [
        "session",
        "step",  # model asks for search_documents + sales_report
        "step",  # search_documents
        "step",  # sales_report
        "step",  # model asks for make_chart
        "step",  # make_chart
        "step",  # model answers
        "answer",
        "done",
    ]
    steps = [data for name, data in stream if name == "step"]
    assert [c["name"] for c in steps[0]["tool_calls"]] == ["search_documents", "sales_report"]
    assert steps[0]["answered"] is False
    assert [s.get("name") for s in steps[1:3]] == ["search_documents", "sales_report"]
    assert all(s["ok"] for s in steps if s["kind"] == "tool")
    assert all("result" not in s for s in steps)  # the inspector reads results
    assert steps[4]["chart_id"] == "chart-1"
    assert steps[5]["answered"] is True

    answer = dict(stream)["answer"]
    assert answer["text"].startswith("The notes [1]")
    assert answer["status"] == "answered"
    assert answer["sources"][0]["rank"] == 1
    assert answer["sources"][0]["filename"] == NOTES
    assert answer["checks"]["citation_status"] == "ok"
    assert [c["id"] for c in answer["charts"]] == ["chart-1"]
    assert dict(stream)["done"]["trace_id"] == answer["trace_id"]


def test_the_answer_is_saved_with_its_trace_and_passages(client, seeded):
    script_hybrid_run(seeded)
    stream = dict(events(client.post("/api/agent/chat", json={"question": QUESTION})))
    done, session_id = stream["done"], stream["session"]["session_id"]

    with seeded.session_factory() as db:
        message = db.get(Message, done["message_id"])
        assert message.agent_trace_id == done["trace_id"]
        assert message.content == stream["answer"]["text"]
        ranks = (
            db.execute(select(MessageSource.rank).where(MessageSource.message_id == message.id))
            .scalars()
            .all()
        )
        # every passage shown, numbered as the answer cites them (invariant 5)
        assert sorted(ranks) == list(range(1, len(stream["answer"]["sources"]) + 1))

    session = client.get(f"/api/sessions/{session_id}").json()
    assistant = session["messages"][-1]
    assert assistant["agent_trace_id"] == done["trace_id"]
    assert assistant["citation_status"] == "ok"
    assert assistant["sources"][0]["filename"] == NOTES


def test_the_trace_endpoint_rebuilds_the_run(client, seeded):
    script_hybrid_run(seeded)
    stream = dict(events(client.post("/api/agent/chat", json={"question": QUESTION})))
    trace = client.get(f"/api/agent/traces/{stream['done']['trace_id']}").json()

    assert trace["question"] == QUESTION
    assert trace["answer"] == stream["answer"]["text"]
    assert trace["iterations"] == 3
    assert trace["prompt_tokens"] == 300 and trace["completion_tokens"] == 30
    assert [s["kind"] for s in trace["steps"]] == ["llm", "tool", "tool", "llm", "tool", "llm"]
    report = trace["steps"][2]
    assert report["name"] == "sales_report"
    assert json.loads(report["result"])["rows"]  # verbatim, as the model saw it
    assert [c["id"] for c in trace["charts"]] == ["chart-1"]
    assert trace["checks"]["citation_status"] == "ok"
    assert client.get("/api/agent/traces/9999").status_code == 404


def test_a_follow_up_goes_into_the_same_session(client, seeded):
    seeded.fake_llm.turns = [turn(content="First.")]
    first = dict(events(client.post("/api/agent/chat", json={"question": "One?"})))
    session_id = first["session"]["session_id"]
    seeded.fake_llm.turns = [turn(content="Second.")]
    second = dict(
        events(client.post("/api/agent/chat", json={"question": "Two?", "session_id": session_id}))
    )

    assert second["session"]["session_id"] == session_id
    roles = [m["role"] for m in client.get(f"/api/sessions/{session_id}").json()["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_the_portal_channel_is_kept(client, seeded):
    seeded.fake_llm.turns = [turn(content="Hola.")]
    stream = dict(
        events(client.post("/api/agent/chat", json={"question": "¿Hola?", "channel": "intranet"}))
    )
    session = client.get(f"/api/sessions/{stream['session']['session_id']}").json()
    assert session["channel"] == "intranet"


def test_a_model_failure_is_an_error_event_and_the_trace_is_kept(client, seeded):
    seeded.fake_llm.turns = [RuntimeError("out of memory")]
    stream = events(client.post("/api/agent/chat", json={"question": "Stock of CH-2004?"}))

    assert [name for name, _ in stream] == ["session", "error"]
    error = stream[-1][1]
    assert "out of memory" in error["detail"]
    with seeded.session_factory() as db:
        assert db.get(AgentTraceRecord, error["trace_id"]).status == "llm_error"
        roles = db.execute(select(Message.role)).scalars().all()
    assert roles == ["user"]  # no assistant message for a failed run


def test_an_unknown_session_is_404_before_the_stream(client):
    response = client.post("/api/agent/chat", json={"question": "Hi", "session_id": 9999})
    assert response.status_code == 404


def test_the_run_is_saved_even_if_nobody_reads_the_stream(seeded):
    """The run doesn't depend on the client: a closed stream still gets its trace."""
    with seeded.session_factory() as db:
        session = history.create_session(db, "Stock?")
        session_id = session.id
    seeded.fake_llm.turns = [turn(content="Saved anyway.")]

    _run_and_save(seeded, "Stock?", session_id, queue.Queue())

    with seeded.session_factory() as db:
        message = db.execute(select(Message).where(Message.session_id == session_id)).scalar_one()
        assert message.content == "Saved anyway."
        assert message.agent_trace_id is not None
