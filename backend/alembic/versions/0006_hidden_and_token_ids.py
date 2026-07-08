"""hidden flag for sites/cameras + public UUIDs for device tokens

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-04

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("sites", "cameras"):
        op.add_column(
            table,
            sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    conn = op.get_bind()
    op.add_column("device_tokens", sa.Column("public_id", sa.Uuid(), nullable=True))
    # Python-side backfill (tiny table) keeps this portable to the sqlite dev
    # flow, where gen_random_uuid() doesn't exist.
    t = sa.table("device_tokens", sa.column("id", sa.Integer()), sa.column("public_id", sa.Uuid()))
    for row_id in conn.execute(sa.select(t.c.id)).scalars().all():
        conn.execute(t.update().where(t.c.id == row_id).values(public_id=uuid.uuid4()))
    with op.batch_alter_table("device_tokens") as batch:
        batch.alter_column("public_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_unique_constraint("uq_device_tokens_public_id", ["public_id"])


def downgrade() -> None:
    with op.batch_alter_table("device_tokens") as batch:
        batch.drop_constraint("uq_device_tokens_public_id")
        batch.drop_column("public_id")
    for table in ("cameras", "sites"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("hidden")
