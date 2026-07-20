"""Restamp existing narrow thumbs with a crisp OSD bar.

Thumbs stamped before the upscale-before-stamp change carry a bar drawn into
the 320px raster — mush once the gallery blows the frame up. The pixels under
the bar are gone (thumbs keep no raw twin), but the bar is a flat band:
osd.restamp() upscales the frame and draws a fresh bar covering the old one.

Unlike 0009 this deliberately calls live app code rather than a frozen copy:
the desired outcome is "current-style crisp bar", not a reproduction of
historical behavior, and restamp() no-ops on frames that don't need or fit
it (wide already, unstamped era, or a bottom edge that doesn't measure like
our bar). Cosmetic and idempotent — a restamped thumb is ≥640 wide and comes
back None on a second pass — so per-photo failures are logged and skipped
rather than blocking the deploy. thumb_size updates ride along, which also
busts the frontend's ?v= image cache key.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-19

"""

import asyncio
import json
import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from trailcam.config import get_settings
from trailcam.osd import restamp
from trailcam.s3 import get_store

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

photos = sa.table(
    "photos",
    sa.column("id", sa.Uuid()),
    sa.column("camera_id", sa.Integer()),
    sa.column("thumb_key", sa.String()),
    sa.column("thumb_size", sa.Integer()),
    sa.column("captured_at", sa.DateTime(timezone=True)),
    sa.column("meta", sa.JSON()),
)

cameras = sa.table(
    "cameras",
    sa.column("id", sa.Integer()),
    sa.column("name", sa.String()),
)


def _utc(ts: datetime) -> datetime:
    # SQLite hands back naive datetimes, Postgres aware ones — normalize.
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


async def _restamp_all(rows) -> list[tuple[object, int]]:
    """Fetch, restamp, and rewrite each thumb; returns (photo id, new size)
    for the ones that changed."""
    store = get_store()
    tz = get_settings().osd_tz
    done: list[tuple[object, int]] = []
    for r in rows:
        try:
            body = await store.get(r.thumb_key)
            if body is None:
                continue
            meta = r.meta if isinstance(r.meta, dict) else json.loads(r.meta) if r.meta else {}
            fresh = restamp(
                body,
                camera_label=r.name,
                captured_at=_utc(r.captured_at),
                temp_c=_num(meta.get("temp_c")),
                battery_v=_num(meta.get("battery_v")),
                tz=tz,
            )
            if fresh is None:
                continue
            await store.put(r.thumb_key, fresh, "image/jpeg")
            done.append((r.id, len(fresh)))
        except Exception:
            logger.warning("restamp failed for photo %s — leaving as-is", r.id, exc_info=True)
    return done


def _in_fresh_loop(coro):
    """Alembic's upgrade() runs inside run_sync on the migration event loop;
    the S3 client needs a loop of its own, in its own thread."""
    box: dict = {}

    def run() -> None:
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001 — re-raised below
            box["e"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["v"]


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(
            photos.c.id,
            photos.c.thumb_key,
            photos.c.captured_at,
            photos.c.meta,
            cameras.c.name,
        )
        .select_from(photos.join(cameras, cameras.c.id == photos.c.camera_id))
        .where(photos.c.thumb_key.is_not(None))
    ).all()
    if not rows:
        return
    done = _in_fresh_loop(_restamp_all(rows))
    for pid, new_size in done:
        conn.execute(photos.update().where(photos.c.id == pid).values(thumb_size=new_size))
    logger.info("restamped %d of %d thumbs", len(done), len(rows))


def downgrade() -> None:
    # Cosmetic and irreversible (the old mushy bar is not worth keeping).
    pass
