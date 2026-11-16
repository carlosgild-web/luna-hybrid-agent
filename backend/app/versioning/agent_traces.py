"""Stores agent runs (invariant 8). Each saved trace also writes one `agent_run`
audit row (invariant 7) naming its status and the tools it called, and one
`external_call` audit row for every request a tool sent outside the machine
(invariant 10, decision 0037): the tools describe those in their step's
`meta["external_call"]` and never write to the database themselves."""

from sqlalchemy.orm import Session

from app.agent.trace import AgentTrace, ToolStep
from app.db.models import AgentTraceRecord
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


def get_trace(db: Session, trace_id: int) -> AgentTraceRecord:
    row = db.get(AgentTraceRecord, trace_id)
    if row is None:
        raise NotFoundError(f"agent trace {trace_id} not found")
    return row


def _describe(step: ToolStep) -> str:
    call = step.meta["external_call"]
    where = f"{step.name}: GET {call['url']}"
    if call.get("ok"):
        return f"{where} ok, rate {call.get('rate')} ({call.get('ms')} ms)"
    return f"{where} failed ({call.get('error')}); answered with the stored value"
