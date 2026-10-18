"""camera_aliases: a retired node slug still resolves to its camera.

A leaf's wire slug comes from its RNS identity, and that identity can be lost
 — the board reappears under a new slug and ingest auto-creates a
second camera row. The merge endpoint moves the split history onto one row and
leaves an alias behind, so the node needs no reflash and its next heartbeat
does not re-create what was just merged.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "camera_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"]),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", "slug", name="uq_camera_aliases_site_slug"),
    )


def downgrade() -> None:
    op.drop_table("camera_aliases")
