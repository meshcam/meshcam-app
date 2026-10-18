"""cameras.last_wire_slug: the name a node actually answers to.

After a merge the kept camera keeps its own slug while the board goes on
announcing the identity it was merged from. Commands are addressed by
slug, so without this every command to a merged camera is sent to a name no
board answers to — re-creating, on the outbound path, exactly the orphaned
command queue the merge was written to repair.

Backfilled to `slug`, which is correct for every node that has never been
re-identified. A merged camera corrects itself on its next contact.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("last_wire_slug", sa.String(length=64), nullable=True))
    op.execute("UPDATE cameras SET last_wire_slug = slug")


def downgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        batch.drop_column("last_wire_slug")
