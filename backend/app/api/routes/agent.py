"""The agent's stored runs (PLAN_HYBRID.md Phase 5). Phase 6 adds the agent chat and
the inspector; for now, the charts a run made, so the chat can render them.

`GET /agent/traces/{trace_id}/charts/{chart_id}` returns the spec `make_chart` stored
in the trace (decision 0038): `{"id", "kind", "title", "y_label", "series", "data"}`,
with `data` one object per label (`{"label": ..., "<series>": value}`), as recharts
reads it.
"""

from typing import Any

from fastapi import APIRouter

from app.agent.tools.charts import chart_specs
from app.api.deps import DbDep
from app.versioning.agent_traces import get_trace
from app.versioning.service import NotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/traces/{trace_id}/charts/{chart_id}")
def get_chart(trace_id: int, chart_id: str, db: DbDep) -> dict[str, Any]:
    specs = chart_specs(get_trace(db, trace_id).steps)
    if chart_id not in specs:
        raise NotFoundError(f"chart {chart_id!r} not found in agent trace {trace_id}")
    return specs[chart_id]
