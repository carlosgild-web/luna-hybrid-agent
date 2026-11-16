"""agent_traces: one row per agent run, with every step

PLAN_HYBRID.md Phase 3, decision 0035.

Revision ID: 0011_agent_traces
Revises: 0010_watched_folders
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_agent_traces"
down_revision: str | None = "0010_watched_folders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_traces_status", "agent_traces", ["status"])
    op.create_index("ix_agent_traces_created_at", "agent_traces", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_traces_created_at", table_name="agent_traces")
    op.drop_index("ix_agent_traces_status", table_name="agent_traces")
    op.drop_table("agent_traces")
