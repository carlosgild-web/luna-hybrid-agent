"""Stores agent runs (invariant 8). Each saved trace also writes one `agent_run`
audit row (invariant 7) naming its status and the tools it called, and one
`external_call` audit row for every request a tool sent outside the machine
(invariant 10, decision 0037): the tools describe those in their step's
`meta["external_call"]` and never write to the database themselves.

An agent chat answer (Phase 6) is saved with `save_agent_answer`: the trace, then the
assistant message pointing to it, with every passage `search_documents` showed as its
sources (invariant 5). A source's rank is the passage number the answer cites, so "[3]"
is the source of rank 3, as in a RAG answer.
"""

from sqlalchemy.orm import Session

from app.agent.answer import shown_passages
from app.agent.trace import AgentTrace, LLMStep, ToolStep
from app.db.models import AgentTraceRecord, Message
from app.versioning import history
from app.versioning.history import SourceRef
from app.versioning.service import NotFoundError, record_audit


def save_trace(db: Session, trace: AgentTrace) -> AgentTraceRecord:
    """Writes the trace and its audit row in one transaction; sets `trace.id`."""
    row = AgentTraceRecord(
        question=trace.question,
        model=trace.model,
        status=trace.status,
        steps=trace.steps_as_dicts(),
        answer=trace.answer,
        error=trace.error,
        iterations=trace.iterations,
        prompt_tokens=trace.prompt_tokens,
        completion_tokens=trace.completion_tokens,
        duration_ms=trace.duration_ms,
        checks=trace.checks,
        created_at=trace.started_at,
    )
    db.add(row)
    db.flush()
    tools = ", ".join(trace.tools_used) or "none"
    record_audit(
        db,
        "agent_run",
        "agent_trace",
        row.id,
        f"{trace.status}, {trace.iterations} model calls, tools: {tools}",
    )
    for step in trace.steps:
        if isinstance(step, ToolStep) and not step.reused and step.meta.get("external_call"):
            record_audit(db, "external_call", "agent_trace", row.id, _describe(step))
    db.commit()
    trace.id = row.id
    return row


def save_agent_answer(
    db: Session, session_id: int, trace: AgentTrace
) -> tuple[AgentTraceRecord, Message]:
    """Stores the run and the assistant message that shows its answer."""
    record = save_trace(db, trace)
    passages = shown_passages(trace)
    message = history.add_message(
        db,
        session_id,
        "assistant",
        trace.answer,
        sources=[
            SourceRef(chunk_id=p["chunk_id"], rank=number, score=p["score"])
            for number, p in sorted(passages.items())
        ],
        agent_trace_id=record.id,
    )
    return record, message


def get_trace(db: Session, trace_id: int) -> AgentTraceRecord:
    row = db.get(AgentTraceRecord, trace_id)
    if row is None:
        raise NotFoundError(f"agent trace {trace_id} not found")
    return row


def load_trace(db: Session, trace_id: int) -> AgentTrace:
    """A stored run as the `AgentTrace` the loop made, so it can be checked and
    scored again (the agent evaluation's rescore)."""
    row = get_trace(db, trace_id)
    trace = AgentTrace(
        question=row.question,
        model=row.model,
        started_at=row.created_at,
        status=row.status,
        answer=row.answer,
        error=row.error,
        duration_ms=row.duration_ms,
        checks=row.checks,
        id=row.id,
    )
    for raw in row.steps:
        step = dict(raw)
        kind = step.pop("kind")
        trace.steps.append(LLMStep(**step) if kind == "llm" else ToolStep(**step))
    return trace


def _describe(step: ToolStep) -> str:
    call = step.meta["external_call"]
    where = f"{step.name}: GET {call['url']}"
    if call.get("ok"):
        return f"{where} ok, rate {call.get('rate')} ({call.get('ms')} ms)"
    return f"{where} failed ({call.get('error')}); answered with the stored value"
