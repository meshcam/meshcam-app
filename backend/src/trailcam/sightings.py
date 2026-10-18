"""Sighting assignment: group burst photos into one "animal visit".

A sighting is a run of same-camera photos whose captured_at gaps stay under
settings.sighting_gap_min. Field data says this is the whole photos-page
noise problem: one walk past the camera is dozens of frames minutes apart,
and grouping them 19x'd a real feed down to something scannable.

Assignment happens at the single Photo-creation site (api/ingest.py), by
neighbor adoption rather than any stored group table — the id IS the group.
Store-and-forward makes arrival order lie about capture order, so a new
frame must look both directions: the frame that arrives last is frequently
the middle of its burst, and when it lands between two groups that each
formed on their own, they were one visit all along and get merged.

The 0009 migration carries its own frozen copy of the gap walk for the
backfill (a migration must not drift with app code).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.models import Photo


async def assign_sighting(
    session: AsyncSession,
    camera_id: int,
    captured_at: datetime,
    gap_min: int,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Sighting id for a new photo: (sighting_id, merged_from | None).

    merged_from is the group that just got absorbed when this frame bridged
    two existing sightings (its rows are UPDATEd here, on the caller's
    session/transaction) — the caller announces the fold to live UIs.

    Known edges, accepted for v1:
    - Two racing ingests could split one burst in two; gateways post
      serially, and any later frame between the halves re-merges them.
    - A leaf RTC frozen at a constant would grow one ever-larger sighting
      for that camera; a max-span guard is a small follow-up if ever seen.
    """
    gap = timedelta(minutes=gap_min)
    prev = await session.scalar(
        select(Photo)
        .where(
            Photo.camera_id == camera_id,
            Photo.captured_at <= captured_at,
            Photo.captured_at >= captured_at - gap,
        )
        .order_by(Photo.captured_at.desc(), Photo.id.desc())
        .limit(1)
    )
    nxt = await session.scalar(
        select(Photo)
        .where(
            Photo.camera_id == camera_id,
            Photo.captured_at >= captured_at,
            Photo.captured_at <= captured_at + gap,
        )
        .order_by(Photo.captured_at.asc(), Photo.id.asc())
        .limit(1)
    )
    if prev is not None and nxt is not None and prev.sighting_id != nxt.sighting_id:
        merged_from = nxt.sighting_id
        await session.execute(
            update(Photo)
            .where(Photo.sighting_id == merged_from)
            .values(sighting_id=prev.sighting_id)
        )
        return prev.sighting_id, merged_from
    if prev is not None:
        return prev.sighting_id, None
    if nxt is not None:
        return nxt.sighting_id, None
    return uuid.uuid4(), None


async def regroup_sightings(session: AsyncSession, camera_id: int, gap_min: int) -> int:
    """Recompute every sighting group for one camera; returns the group count.

    assign_sighting() only ever looks at a new photo's immediate neighbours,
    which is right at ingest and wrong after a merge: two cameras' captures
    have just landed in one camera, interleaved, and the groups they arrived
    with were formed against different neighbours. Same gap walk as the 0009
    backfill, over one camera.
    """
    rows = (
        await session.scalars(
            select(Photo).where(Photo.camera_id == camera_id).order_by(Photo.captured_at, Photo.id)
        )
    ).all()
    gap = timedelta(minutes=gap_min)
    groups = 0
    prev_at: datetime | None = None
    sid = uuid.uuid4()
    for photo in rows:
        at = photo.captured_at
        # SQLite hands back naive datetimes, Postgres aware ones.
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if prev_at is None or at - prev_at > gap:
            sid = uuid.uuid4()
            groups += 1
        photo.sighting_id = sid
        prev_at = at
    return groups
