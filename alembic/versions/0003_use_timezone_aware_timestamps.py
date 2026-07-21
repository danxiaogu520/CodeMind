"""Use timezone-aware UTC audit timestamps.

Revision ID: 0003_timezone_timestamps
Revises: 0002_phase1_knowledge_base
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_timezone_timestamps"
down_revision: str | Sequence[str] | None = "0002_phase1_knowledge_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP_COLUMNS = {
    "repositories": ("created_at", "updated_at"),
    "index_versions": ("created_at", "activated_at"),
    "index_jobs": ("created_at", "started_at", "finished_at", "heartbeat_at"),
}


def upgrade() -> None:
    for table, columns in TIMESTAMP_COLUMNS.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                type_=sa.DateTime(timezone=True),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )


def downgrade() -> None:
    for table, columns in TIMESTAMP_COLUMNS.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                type_=sa.DateTime(timezone=False),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )
