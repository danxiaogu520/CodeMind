"""Create the Phase 0 schema baseline.

Revision ID: 0001_phase0_baseline
Revises:
Create Date: 2026-07-20
"""

from collections.abc import Sequence

revision: str = "0001_phase0_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish a versioned baseline before Phase 1 domain tables are added."""


def downgrade() -> None:
    """The empty baseline has no database objects to remove."""
