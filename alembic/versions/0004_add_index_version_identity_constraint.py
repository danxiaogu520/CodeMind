"""Add the immutable index version identity constraint.

Revision ID: 0004_index_version_identity
Revises: 0003_timezone_timestamps
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_index_version_identity"
down_revision: str | None = "0003_timezone_timestamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_index_versions_repository_id",
        "index_versions",
        ["repository_id", "commit_sha", "parser_version", "embedding_model"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_index_versions_repository_id",
        "index_versions",
        type_="unique",
    )
