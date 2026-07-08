"""command queue (gateway-pulled node work, e.g. fetch full-res)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=True),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_commands_status", "commands", ["status"])
    op.create_index("ix_commands_event_id", "commands", ["event_id"])


def downgrade() -> None:
    op.drop_table("commands")
