"""feed orders by received_at — index to match

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-02

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_photos_received_at_id", "photos", ["received_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_photos_received_at_id", table_name="photos")
