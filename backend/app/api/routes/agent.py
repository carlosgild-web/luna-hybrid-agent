"""The agent in the chat, and its stored runs (PLAN_HYBRID.md Phases 5 and 6).

`POST /agent/chat` asks Luna with tools and streams the run as server-sent events:
  session  {"session_id", "user_message_id"}
  step     one per model call or tool call, as it ends (see `step_event`)
  answer   {"text", "status", "trace_id", "message_id", "sources", "checks", "charts"}
  done     {"message_id", "trace_id", "status"}
  error    {"detail", "trace_id"}   (instead of answer and done, if the model failed)

A model step that requests tools is the cue the UI shows "🔧 sales_report…" on; the
tool step that follows closes it. The run happens in its own thread, so a client that
leaves mid-run doesn't stop it: the trace and the answer are saved either way
(invariant 8). Steps on the stream are summaries; the stored trace has every tool
result verbatim (invariant 11) and is what the inspector reads.

`GET /agent/traces/{trace_id}` returns the stored run: its steps, totals, answer
check and chart specs. `GET /agent/traces/{trace_id}/charts/{chart_id}` returns one
chart spec `make_chart` stored in the trace (decision 0038): `{"id", "kind", "title",
"y_label", "series", "data"}`, with `data` one object per label (`{"label": ...,
"<series>": value}`), as recharts reads it.
"""

import logging
import queue
import threading
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agent.answer import shown_passages
from app.agent.loop import AgentLimits, run_agent
from app.agent.registry import ToolContext
from app.agent.tools import build_registry
from app.agent.tools.charts import chart_specs
from app.agent.trace import AgentTrace, LLMStep, Step
from app.api.deps import DbDep, RuntimeDep
from app.api.schemas import AgentChatIn, AgentTraceOut
from app.api.sse import SSE_HEADERS, drive_sync_stream, sse
from app.core.runtime import Runtime
from app.retrieval.library import DocumentLibrary
from app.versioning import history
from app.versioning.agent_traces import get_trace, save_agent_answer, save_trace
from app.versioning.service import NotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)

_END = None


@router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream"}},
)
def agent_chat(db: DbDep, runtime: RuntimeDep, body: AgentChatIn):
    """Answers with tools (documents, business data, FX, charts), streaming each step.
    An unknown session is a normal HTTP error, before the stream opens."""
    existing = history.get_session(db, body.session_id) if body.session_id is not None else None
    session = existing or history.create_session(db, body.question, channel=body.channel)
    user_message = history.add_message(db, session.id, "user", body.question)
    stream = _agent_stream(
        runtime, body.question, session_id=session.id, user_message_id=user_message.id
    )
    return StreamingResponse(
        drive_sync_stream(stream), media_type="text/event-stream", headers=SSE_HEADERS
    )


def _agent_stream(
    runtime: Runtime, question: str, *, session_id: int, user_message_id: int
) -> Iterator[str]:
    yield sse("session", {"session_id": session_id, "user_message_id": user_message_id})
    events: queue.Queue[tuple[str, Any] | None] = queue.Queue()
    threading.Thread(
        target=_run_and_save,
        args=(runtime, question, session_id, events),
        name="agent-run",
        daemon=True,
    ).start()
    while (event := events.get()) is not _END:
        yield sse(*event)


def _run_and_save(
    runtime: Runtime, question: str, session_id: int, events: "queue.Queue[Any]"
) -> None:
    """The run, in its own thread: its steps go on `events` as they happen."""
    settings = runtime.settings
    model = settings.agent_chat_model
    trace_id: int | None = None
    try:
        documents = DocumentLibrary.from_runtime(runtime)
        trace = run_agent(
            question,
            llm=runtime.llm_client_for(model),
            registry=build_registry(documents),
            ctx=ToolContext(settings, documents=documents),
            limits=AgentLimits.from_settings(settings),
            model=model,
            on_step=lambda step: events.put(("step", step_event(step))),
        )
        with runtime.session_factory() as db:
            if trace.status == "llm_error":
                trace_id = save_trace(db, trace).id
                events.put(("error", {"detail": _model_failed(trace), "trace_id": trace_id}))
                return
            record, message = save_agent_answer(db, session_id, trace)
            trace_id = record.id
            events.put(("answer", answer_event(trace, message.id)))
            events.put(
                ("done", {"message_id": message.id, "trace_id": trace_id, "status": trace.status})
            )
    except Exception as exc:
        logger.exception("agent run failed", extra={"session_id": session_id})
        detail = f"The agent run failed ({type(exc).__name__}). Check the server log."
        events.put(("error", {"detail": detail, "trace_id": trace_id}))
    finally:
        events.put(_END)


def step_event(step: Step) -> dict[str, Any]:
    """What the chat shows of a step while the run goes on: no tool results (the
    inspector reads those from the stored trace)."""
    if isinstance(step, LLMStep):
        return {
            "kind": "llm",
            "iteration": step.iteration,
            "tool_calls": step.tool_calls,
            "answered": not step.tool_calls and bool(step.content.strip()),
            "prompt_tokens": step.prompt_tokens,
            "completion_tokens": step.completion_tokens,
            "ms": step.ms,
        }
    event: dict[str, Any] = {
        "kind": "tool",
        "iteration": step.iteration,
        "name": step.name,
        "arguments": step.arguments,
        "ok": step.ok,
        "reused": step.reused,
        "ms": step.ms,
    }
    if step.ok and (chart := step.meta.get("chart")):
        event["chart_id"] = chart["id"]
    return event


def answer_event(trace: AgentTrace, message_id: int) -> dict[str, Any]:
    return {
        "text": trace.answer,
        "status": trace.status,
        "trace_id": trace.id,
        "message_id": message_id,
        "sources": passage_sources(shown_passages(trace)),
        "checks": trace.checks,
        "charts": list(chart_specs(trace.steps_as_dicts()).values()),
    }


def passage_sources(passages: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """The passages a run showed, shaped as the chat's sources (`SourceOut`)."""
    return [
        {
            "rank": number,
            "chunk_id": p["chunk_id"],
            "score": p["score"],
            "document_id": p["document_id"],
            "filename": p["filename"],
            "page": p["page"],
            "heading": p["heading"],
        }
        for number, p in sorted(passages.items())
    ]


def _model_failed(trace: AgentTrace) -> str:
    return f"The model ({trace.model}) failed: {trace.error or 'no reply'}"


@router.get("/traces/{trace_id}", response_model=AgentTraceOut)
def get_agent_trace(trace_id: int, db: DbDep) -> AgentTraceOut:
    """The stored run, everything the inspector needs to rebuild the answer."""
    row = get_trace(db, trace_id)
    return AgentTraceOut(
        id=row.id,
        question=row.question,
        model=row.model,
        status=row.status,
        answer=row.answer,
        error=row.error,
        iterations=row.iterations,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        checks=row.checks,
        steps=row.steps,
        charts=list(chart_specs(row.steps).values()),
    )


@router.get("/traces/{trace_id}/charts/{chart_id}")
def get_chart(trace_id: int, chart_id: str, db: DbDep) -> dict[str, Any]:
    specs = chart_specs(get_trace(db, trace_id).steps)
    if chart_id not in specs:
        raise NotFoundError(f"chart {chart_id!r} not found in agent trace {trace_id}")
    return specs[chart_id]
