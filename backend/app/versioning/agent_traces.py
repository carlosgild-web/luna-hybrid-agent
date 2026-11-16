"""Stores agent runs (invariant 8). Each saved trace also writes one `agent_run`
audit row (invariant 7) naming its status and the tools it called."""

from sqlalchemy.orm import Session

from app.agent.trace import AgentTrace
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
    db.commit()
    trace.id = row.id
    return row


def get_trace(db: Session, trace_id: int) -> AgentTraceRecord:
    row = db.get(AgentTraceRecord, trace_id)
    if row is None:
        raise NotFoundError(f"agent trace {trace_id} not found")
    return row
