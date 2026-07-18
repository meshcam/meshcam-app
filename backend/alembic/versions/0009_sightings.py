"""photos.sighting_id: burst grouping ("one animal visit") + backfill.

Photos from one camera whose captured_at gaps stay under the sighting gap
share a sighting_id; the feed shows one tile per group. Assignment lives in
trailcam/sightings.py at ingest; this backfill is a frozen copy of the same
30-minute gap walk (a migration must not drift with app code — and the
threshold is frozen at the migration-time default on purpose: a later
setting change reshapes future photos only).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-15

"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GAP_S = 30 * 60  # frozen migration-time default for TRAILCAM_SIGHTING_GAP_MIN

photos = sa.table(
    "photos",
    sa.column("id", sa.Uuid()),
    sa.column("camera_id", sa.Integer()),
    sa.column("captured_at", sa.DateTime(timezone=True)),
    sa.column("sighting_id", sa.Uuid()),
)


def _utc(ts: datetime) -> datetime:
    # SQLite hands back naive datetimes, Postgres aware ones — normalize
    # before subtracting.
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def _backfill(conn) -> None:
    """Per camera, walk captures in order; a gap over GAP_S starts a group."""
    rows = conn.execute(
        sa.select(photos.c.id, photos.c.camera_id, photos.c.captured_at).order_by(
            photos.c.camera_id, photos.c.captured_at, photos.c.id
        )
    ).all()
    group: list = []
    prev_cam: int | None = None
    prev_at: datetime | None = None

    def flush() -> None:
        if group:
            conn.execute(
                photos.update()
                .where(photos.c.id.in_(group))
                .values(sighting_id=uuid.uuid4())
            )

    for pid, cam_id, captured_at in rows:
        at = _utc(captured_at)
        if cam_id != prev_cam or prev_at is None or (at - prev_at).total_seconds() > GAP_S:
            flush()
            group = []
        group.append(pid)
        prev_cam, prev_at = cam_id, at
    flush()


def upgrade() -> None:
    op.add_column("photos", sa.Column("sighting_id", sa.Uuid(), nullable=True))
    _backfill(op.get_bind())
    # batch_alter_table: SQLite can't ALTER to NOT NULL in place (table rebuild).
    with op.batch_alter_table("photos") as batch:
        batch.alter_column("sighting_id", existing_type=sa.Uuid(), nullable=False)
    op.create_index("ix_photos_sighting_id", "photos", ["sighting_id"])


def downgrade() -> None:
    op.drop_index("ix_photos_sighting_id", table_name="photos")
    with op.batch_alter_table("photos") as batch:
        batch.drop_column("sighting_id")
