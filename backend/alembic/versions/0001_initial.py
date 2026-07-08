"""initial schema: sites, cameras, device_tokens, photos

Revision ID: 0001
Revises:
Create Date: 2026-07-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
    )
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_battery_v", sa.Float(), nullable=True),
        sa.UniqueConstraint("site_id", "slug", name="uq_cameras_site_slug"),
    )
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.String(128), nullable=False, unique=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("thumb_key", sa.String(512), nullable=True),
        sa.Column("full_key", sa.String(512), nullable=True),
        sa.Column("thumb_size", sa.Integer(), nullable=True),
        sa.Column("full_size", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("meta", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
        sa.Column("keep", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_photos_captured_at_id", "photos", ["captured_at", "id"])
    op.create_index("ix_photos_camera_captured", "photos", ["camera_id", "captured_at"])
    op.create_index("ix_photos_expires_at", "photos", ["expires_at"])


def downgrade() -> None:
    op.drop_table("photos")
    op.drop_table("device_tokens")
    op.drop_table("cameras")
    op.drop_table("sites")
