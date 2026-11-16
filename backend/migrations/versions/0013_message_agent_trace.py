"""messages.agent_trace_id: the agent run behind an assistant message

PLAN_HYBRID.md Phase 6, decision 0039.

Revision ID: 0013_message_agent_trace
Revises: 0012_agent_trace_checks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_message_agent_trace"
down_revision: str | None = "0012_agent_trace_checks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("agent_trace_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_messages_agent_trace_id", "agent_traces", ["agent_trace_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("fk_messages_agent_trace_id", type_="foreignkey")
        batch.drop_column("agent_trace_id")
