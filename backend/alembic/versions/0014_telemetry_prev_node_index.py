"""ix_telemetry_prev_node: find a re-identification beat without reading the table.

/nodes/health asks, unwindowed, for the newest telemetry row per node that
carries extra.prev_node (see identity.py for why there is no window).
No index answered that, so every poll seq-scanned the whole retained table to
return almost nothing. This partial index holds only the rows that carry the
key, in the order the query wants them.

THE PREDICATE IS COPIED, NOT EQUIVALENT-TO, the query's. Postgres will only use
a partial index when it can prove the query's predicate implies the index's, and
it proves that by comparing expression trees. Measured on the dev database
(PostgreSQL 18.3, 140,860 rows): an index spelled `WHERE extra ? 'prev_node'` —
the obvious way to write "has the key" — was never used, and neither was
`WHERE (extra ->> 'prev_node') IS NOT NULL`, because SQLAlchemy compiles the
query's `.as_string()` to a `::character varying` relabel that neither form
matches. Spelled with the cast, as below, the same query drops from a 57 ms
parallel seq scan over 11,587 buffers to a 0.08 ms index scan over 11.

So this string has to track what trailcam.api.telemetry.PREV_NODE_PRESENT
compiles to on Postgres. tests/test_node_health_query.py asserts they agree, and
fails loudly if a SQLAlchemy upgrade moves one without the other.

Postgres only: `->>` and partial indexes are not SQLite, and the test suite runs
on SQLite. A plain CREATE INDEX, not CONCURRENTLY: the build took 83 ms on the
largest instance and yields a 16 kB index, so the write lock it takes in the
migrate initContainer is shorter than the deploy's own rollout, and CONCURRENTLY
would trade that for an autocommit block that can strand an INVALID index.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_telemetry_prev_node"

# Must equal the compiled Postgres text of telemetry.PREV_NODE_PRESENT.
PREV_NODE_PREDICATE = "CAST((telemetry.extra ->> 'prev_node') AS VARCHAR) IS NOT NULL"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_index(
        INDEX_NAME,
        "telemetry",
        ["camera_id", sa.text("received_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text(PREV_NODE_PREDICATE),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_index(INDEX_NAME, table_name="telemetry")
