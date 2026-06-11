"""Add security_findings and security_scan_history tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11

CP-4 requirement: scan history and findings stored in Postgres with timestamps.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # security_findings: one row per finding per scan
    op.create_table(
        "security_findings",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=False), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=False), nullable=True),
        sa.Column("scan_id", sa.String(64), nullable=False),          # FK to scan_history.scan_id
        sa.Column("pattern", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("suggested_fix", sa.Text, nullable=True),
        sa.Column("node_path", JSONB, nullable=True),                  # ordered list of KG node IDs
        sa.Column("source_node_id", sa.String(256), nullable=True),
        sa.Column("sink_node_id", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_security_findings_project_id", "security_findings", ["project_id"])
    op.create_index("ix_security_findings_task_id", "security_findings", ["task_id"])
    op.create_index("ix_security_findings_severity", "security_findings", ["severity"])
    op.create_index("ix_security_findings_scan_id", "security_findings", ["scan_id"])

    # security_scan_history: one row per scan run
    op.create_table(
        "security_scan_history",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("scan_id", sa.String(64), nullable=False, unique=True),
        sa.Column("project_id", UUID(as_uuid=False), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=False), nullable=True),
        sa.Column("findings_count", sa.Integer, nullable=False, default=0),
        sa.Column("critical_count", sa.Integer, nullable=False, default=0),
        sa.Column("high_count", sa.Integer, nullable=False, default=0),
        sa.Column("node_count", sa.Integer, nullable=True),
        sa.Column("edge_count", sa.Integer, nullable=True),
        sa.Column("scanned_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_security_scan_history_project_id", "security_scan_history", ["project_id"])
    op.create_index("ix_security_scan_history_scanned_at", "security_scan_history", ["scanned_at"])


def downgrade() -> None:
    op.drop_table("security_findings")
    op.drop_table("security_scan_history")
