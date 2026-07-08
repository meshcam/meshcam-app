"""public UUIDs for sites + cameras (user-facing ids; int PKs stay for FK joins)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-03

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    for table in ("sites", "cameras"):
        op.add_column(table, sa.Column("public_id", sa.Uuid(), nullable=True))
        # Python-side backfill (tiny tables) keeps this portable to the sqlite
        # dev flow, where gen_random_uuid() doesn't exist.
        t = sa.table(table, sa.column("id", sa.Integer()), sa.column("public_id", sa.Uuid()))
        for row_id in conn.execute(sa.select(t.c.id)).scalars().all():
            conn.execute(t.update().where(t.c.id == row_id).values(public_id=uuid.uuid4()))
        # batch mode: plain ALTERs on Postgres, table rebuild on sqlite (which
        # can't SET NOT NULL / ADD CONSTRAINT in place)
        with op.batch_alter_table(table) as batch:
            batch.alter_column("public_id", existing_type=sa.Uuid(), nullable=False)
            batch.create_unique_constraint(f"uq_{table}_public_id", ["public_id"])


def downgrade() -> None:
    for table in ("cameras", "sites"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"uq_{table}_public_id")
            batch.drop_column("public_id")
