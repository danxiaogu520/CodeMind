"""Add hierarchical memory and incremental-index statistics.

Revision ID: 0006_memory_incremental
Revises: 0005_agent_runs
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_memory_incremental"
down_revision: str | Sequence[str] | None = "0005_agent_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "index_jobs",
        sa.Column("delta_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(36),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "index_version_id",
            sa.String(36),
            sa.ForeignKey("index_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layer", sa.String(20), nullable=False),
        sa.Column("scope_key", sa.String(300), nullable=False),
        sa.Column("session_id", sa.String(100)),
        sa.Column("session_key", sa.String(100), server_default="", nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("source_paths_json", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("source_hashes_json", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("stale", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "repository_id",
            "index_version_id",
            "layer",
            "scope_key",
            "session_key",
            name="uq_memories_scope",
        ),
    )
    op.create_index("ix_memories_repository_id", "memories", ["repository_id"])
    op.create_index("ix_memories_index_version_id", "memories", ["index_version_id"])
    op.create_index("ix_memories_session_id", "memories", ["session_id"])
    op.create_index("ix_memories_run_id", "memories", ["run_id"])
    op.create_index(
        "ix_memories_recall",
        "memories",
        ["repository_id", "index_version_id", "layer", "stale"],
    )


def downgrade() -> None:
    op.drop_table("memories")
    op.drop_column("index_jobs", "delta_json")
