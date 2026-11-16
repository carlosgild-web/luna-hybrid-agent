"""agent_traces.checks: the answer's citation and number check

PLAN_HYBRID.md Phase 4, decision 0036.

Revision ID: 0012_agent_trace_checks
Revises: 0011_agent_traces
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_agent_trace_checks"
down_revision: str | None = "0011_agent_traces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_traces") as batch:
        batch.add_column(sa.Column("checks", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_traces") as batch:
        batch.drop_column("checks")
