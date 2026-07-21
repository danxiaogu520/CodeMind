"""Create repository knowledge base tables.

Revision ID: 0002_phase1_knowledge_base
Revises: 0001_phase0_baseline
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase1_knowledge_base"
down_revision: str | Sequence[str] | None = "0001_phase0_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("default_ref", sa.String(255)),
        sa.Column("active_index_version_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "index_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(36),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_at", sa.DateTime()),
    )
    op.create_index("ix_index_versions_repository_id", "index_versions", ["repository_id"])
    op.create_index(
        "ix_index_versions_repository_status", "index_versions", ["repository_id", "status"]
    )
    op.create_table(
        "index_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(36),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_ref", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("heartbeat_at", sa.DateTime()),
    )
    op.create_index("ix_index_jobs_repository_id", "index_jobs", ["repository_id"])
    op.create_index("ix_index_jobs_claim", "index_jobs", ["status", "created_at"])
    op.create_table(
        "source_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("index_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("language", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("parse_status", sa.String(20), nullable=False),
        sa.Column("error_summary", sa.Text()),
        sa.UniqueConstraint("version_id", "path"),
    )
    op.create_index("ix_source_files_version_id", "source_files", ["version_id"])
    op.create_table(
        "symbols",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "file_id",
            sa.String(36),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("qualified_name", sa.String(1000), nullable=False),
        sa.Column("short_name", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
    )
    op.create_index("ix_symbols_file_id", "symbols", ["file_id"])
    op.create_index("ix_symbols_file_qualified", "symbols", ["file_id", "qualified_name"])
    op.create_index("ix_symbols_short_name", "symbols", ["short_name"])
    op.create_table(
        "code_chunks",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column("chunk_id", sa.String(36), nullable=False),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("index_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            sa.String(36),
            sa.ForeignKey("source_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol_id", sa.String(36), sa.ForeignKey("symbols.id", ondelete="SET NULL")),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("language", sa.String(30), nullable=False),
        sa.Column("part", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
    )
    op.create_index("ix_code_chunks_version_id", "code_chunks", ["version_id"])
    op.create_index("ix_code_chunks_file_id", "code_chunks", ["file_id"])
    op.create_index("ix_code_chunks_symbol_id", "code_chunks", ["symbol_id"])
    op.create_index("ix_code_chunks_version_path", "code_chunks", ["version_id", "path"])
    op.create_index("ix_code_chunks_chunk_id", "code_chunks", ["chunk_id"])
    op.create_table(
        "relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("index_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_symbol_id", sa.String(36), sa.ForeignKey("symbols.id", ondelete="CASCADE")
        ),
        sa.Column(
            "target_symbol_id", sa.String(36), sa.ForeignKey("symbols.id", ondelete="SET NULL")
        ),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
    )
    op.create_index("ix_relations_version_id", "relations", ["version_id"])
    op.create_index("ix_relations_source_symbol_id", "relations", ["source_symbol_id"])
    op.create_index("ix_relations_target_symbol_id", "relations", ["target_symbol_id"])
    op.create_index("ix_relations_version_type", "relations", ["version_id", "type"])


def downgrade() -> None:
    op.drop_table("relations")
    op.drop_table("code_chunks")
    op.drop_table("symbols")
    op.drop_table("source_files")
    op.drop_table("index_jobs")
    op.drop_table("index_versions")
    op.drop_table("repositories")
