"""node kind + telemetry heartbeats

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("kind", sa.String(16), nullable=False, server_default="camera"),
    )
    op.create_table(
        "telemetry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("battery_v", sa.Float(), nullable=True),
        sa.Column("temp_c", sa.Float(), nullable=True),
        sa.Column("pressure_hpa", sa.Float(), nullable=True),
        sa.Column("rssi", sa.Float(), nullable=True),
        sa.Column("snr", sa.Float(), nullable=True),
        sa.Column("uptime_s", sa.Integer(), nullable=True),
        sa.Column("boot_reason", sa.String(32), nullable=True),
        sa.Column("fw_version", sa.String(64), nullable=True),
        sa.Column("extra", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
    )
    op.create_index("ix_telemetry_camera_received", "telemetry", ["camera_id", "received_at"])
    op.create_index("ix_telemetry_received_at", "telemetry", ["received_at"])


def downgrade() -> None:
    op.drop_table("telemetry")
    op.drop_column("cameras", "kind")
