"""photo tags (species labels): tags + photo_tags association

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
    )
    op.create_table(
        "photo_tags",
        sa.Column(
            "photo_id",
            sa.Uuid(),
            sa.ForeignKey("photos.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_photo_tags_tag_id", "photo_tags", ["tag_id"])


def downgrade() -> None:
    op.drop_index("ix_photo_tags_tag_id", table_name="photo_tags")
    op.drop_table("photo_tags")
    op.drop_table("tags")
