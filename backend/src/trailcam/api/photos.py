import base64
import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from trailcam.auth import current_user
from trailcam.config import get_settings
from trailcam.db import get_session
from trailcam.events import COMMANDS, FEED, bus, publish_request_update
from trailcam.models import OUTSTANDING, Camera, Command, Photo, Site, Tag, photo_tags, utcnow
from trailcam.refs import resolve_uuid_ref
from trailcam.s3 import get_store, raw_variant
from trailcam.schemas import (
    FullRequestOut,
    HistogramBucketOut,
    KeepIn,
    PhotoOut,
    PhotoPage,
    RequestFullIn,
    SightingOut,
    SightingPage,
    TagCountOut,
    TagIn,
    TagOut,
)

router = APIRouter(prefix="/api/v1", tags=["photos"], dependencies=[Depends(current_user)])

# Demo mode only: max fetch_full commands in flight across all visitors —
# the simulated mesh is one radio channel, so the queue must stay bounded.
DEMO_MAX_OUTSTANDING = 8


def _photo_out(p: Photo, requested: set[str] | None = None) -> PhotoOut:
    return PhotoOut(
        id=p.id,
        event_id=p.event_id,
        sighting_id=p.sighting_id,
        camera_id=p.camera.public_id,
        camera_name=p.camera.name,
        site_slug=p.camera.site.slug,
        captured_at=p.captured_at,
        received_at=p.received_at,
        keep=p.keep,
        expires_at=p.expires_at,
        has_full=p.full_key is not None,
        full_requested=bool(requested and p.event_id in requested),
        thumb_size=p.thumb_size,
        full_size=p.full_size,
        meta=p.meta,
        tags=[TagOut(slug=t.slug, name=t.name) for t in p.tags],
    )


async def _outstanding_full_requests(session: AsyncSession, photos: list[Photo]) -> set[str]:
    # No full_key filter: a quality=max re-request can be outstanding on a
    # photo that already has a (standard) full.
    ids = [p.event_id for p in photos]
    if not ids:
        return set()
    rows = await session.scalars(
        select(Command.event_id).where(
            Command.event_id.in_(ids),
            Command.kind == "fetch_full",
            Command.status.in_(OUTSTANDING),
        )
    )
    return set(rows.all())


def _encode_cursor(ts: datetime, ref: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{ref}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts, pid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(pid)
    except Exception as exc:
        raise HTTPException(422, "bad cursor") from exc


async def _get_photo(session: AsyncSession, photo_ref: str) -> Photo:
    return await resolve_uuid_ref(
        session,
        select(Photo).options(joinedload(Photo.camera).joinedload(Camera.site)),
        Photo.id,
        photo_ref,
        "photo",
    )


async def _photo_criteria(
    session: AsyncSession,
    site: str | None,
    camera_id: str | None,
    kept: bool | None,
    tag: str | None,
    captured_after: datetime | None,
    captured_before: datetime | None,
) -> list:
    """The feed's filter set as pure WHERE criteria on Photo columns
    (IN-subqueries instead of joins) so the same list composes unchanged
    into the flat feed, the GROUP BY sightings feed, and the histogram."""
    crit = []
    if site:
        crit.append(
            Photo.camera_id.in_(select(Camera.id).join(Camera.site).where(Site.slug == site))
        )
    if camera_id is not None:
        cam = await resolve_uuid_ref(session, select(Camera), Camera.public_id, camera_id, "camera")
        crit.append(Photo.camera_id == cam.id)
    if kept:
        crit.append(Photo.keep.is_(True))
    if tag:
        crit.append(
            Photo.id.in_(
                select(photo_tags.c.photo_id)
                .join(Tag, Tag.id == photo_tags.c.tag_id)
                .where(Tag.slug == tag)
            )
        )
    if captured_after is not None:
        crit.append(Photo.captured_at >= captured_after)
    if captured_before is not None:
        crit.append(Photo.captured_at < captured_before)
    return crit


@router.get("/photos", response_model=PhotoPage)
async def list_photos(
    site: str | None = None,
    camera_id: str | None = None,
    kept: bool | None = None,
    tag: str | None = None,
    captured_after: datetime | None = None,
    captured_before: datetime | None = None,
    before: str | None = None,
    after: str | None = None,
    anchor: datetime | None = None,
    direction: str = "older",
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    # Feed order = arrival order (received_at): leaf clocks drift/freeze (no NTP
    # in the woods) and store-and-forward delivers late — the server clock is
    # the only monotonic truth. captured_at is display metadata — but it IS what
    # the captured_after/before window filters on, because "what moved Tuesday
    # night" is a question about capture time, drift and all.
    #
    # Bidirectional paging (the time scrubber): `anchor` starts a page at an
    # arbitrary instant in the stream instead of the top, and direction=newer /
    # an `after` cursor walk it back toward now. anchor partitions cleanly —
    # received_at < anchor goes down, >= anchor goes up — so the two directions
    # never overlap. Items are always newest-first; next_cursor continues
    # whichever direction was queried.
    limit = min(max(limit, 1), 200)
    newer = after is not None or direction == "newer"
    q = (
        select(Photo)
        .options(joinedload(Photo.camera).joinedload(Camera.site))
        .where(*await _photo_criteria(
            session, site, camera_id, kept, tag, captured_after, captured_before
        ))
        .limit(limit + 1)
    )
    if newer:
        q = q.order_by(Photo.received_at.asc(), Photo.id.asc())
        if after:
            ts, pid = _decode_cursor(after)
            q = q.where(tuple_(Photo.received_at, Photo.id) > (ts, pid))
        elif anchor is not None:
            q = q.where(Photo.received_at >= anchor)
    else:
        q = q.order_by(Photo.received_at.desc(), Photo.id.desc())
        if before:
            ts, pid = _decode_cursor(before)
            q = q.where(tuple_(Photo.received_at, Photo.id) < (ts, pid))
        elif anchor is not None:
            q = q.where(Photo.received_at < anchor)
    rows = (await session.scalars(q)).all()
    page, more = rows[:limit], len(rows) > limit
    edge = page[-1] if page else None  # last row in scan order = where the cursor resumes
    if newer:
        page = list(reversed(page))
    requested = await _outstanding_full_requests(session, page)
    return PhotoPage(
        items=[_photo_out(p, requested) for p in page],
        next_cursor=_encode_cursor(edge.received_at, edge.id) if more and edge else None,
    )


@router.get("/photos/histogram", response_model=list[HistogramBucketOut])
async def photos_histogram(
    site: str | None = None,
    camera_id: str | None = None,
    kept: bool | None = None,
    tag: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Hourly capture counts under the active filters — the data behind the
    photos-page activity strip (which re-buckets to screen resolution and
    owns the timezone question; UTC hours re-bucket cleanly into any
    whole-hour zone). Declared before /photos/{photo_ref} so "histogram"
    never parses as a ref. Bucketing is Python-side: portable across
    SQLite/Postgres (same tradeoff as 0008's backfill filter), and one
    timestamp column for a season of photos is a small fetch."""
    crit = await _photo_criteria(session, site, camera_id, kept, tag, None, None)
    counts: dict[datetime, int] = {}
    for t in (await session.scalars(select(Photo.captured_at).where(*crit))).all():
        t = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        hour = t.replace(minute=0, second=0, microsecond=0)
        counts[hour] = counts.get(hour, 0) + 1
    return [HistogramBucketOut(hour=h, count=c) for h, c in sorted(counts.items())]


# --- Sightings ------------------------------------------------------------------
#
# The grouped feed: one item per burst (models.Photo.sighting_id). Same
# filters and the same arrival-order invariant as /photos — a sighting sorts
# by the newest arrival among its matching frames, so a straggler surfaces
# its whole visit at the top, honestly. Filter-then-group on purpose: under
# kept=1 or tag=, count/cover reflect the matching frames only ("show me
# saved bucks" works with no special casing).


@router.get("/sightings", response_model=SightingPage)
async def list_sightings(
    site: str | None = None,
    camera_id: str | None = None,
    kept: bool | None = None,
    tag: str | None = None,
    captured_after: datetime | None = None,
    captured_before: datetime | None = None,
    before: str | None = None,
    after: str | None = None,
    anchor: datetime | None = None,
    direction: str = "older",
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    limit = min(max(limit, 1), 200)
    newer = after is not None or direction == "newer"
    crit = await _photo_criteria(
        session, site, camera_id, kept, tag, captured_after, captured_before
    )
    last_received = func.max(Photo.received_at).label("last_received_at")
    agg = (
        select(
            Photo.sighting_id,
            func.count().label("count"),
            func.sum(case((Photo.keep.is_(True), 1), else_=0)).label("kept_count"),
            func.min(Photo.captured_at).label("started_at"),
            func.max(Photo.captured_at).label("ended_at"),
            last_received,
        )
        .where(*crit)
        .group_by(Photo.sighting_id)
        .limit(limit + 1)
    )
    # A sighting sorts by its newest matching frame, so anchor partitions on
    # max(received_at): a burst straddling the anchor lands on the newer side,
    # whole — never split, never doubled.
    if newer:
        agg = agg.order_by(last_received.asc(), Photo.sighting_id.asc())
        if after:
            ts, sid = _decode_cursor(after)
            agg = agg.having(tuple_(func.max(Photo.received_at), Photo.sighting_id) > (ts, sid))
        elif anchor is not None:
            agg = agg.having(func.max(Photo.received_at) >= anchor)
    else:
        agg = agg.order_by(last_received.desc(), Photo.sighting_id.desc())
        if before:
            ts, sid = _decode_cursor(before)
            agg = agg.having(tuple_(func.max(Photo.received_at), Photo.sighting_id) < (ts, sid))
        elif anchor is not None:
            agg = agg.having(func.max(Photo.received_at) < anchor)
    rows = (await session.execute(agg)).all()
    page, more = rows[:limit], len(rows) > limit
    edge = page[-1] if page else None
    if newer:
        page = list(reversed(page))

    # Covers: earliest-captured matching frame of each page sighting (the
    # burst usually opens with the animal entering frame).
    rn = (
        func.row_number()
        .over(partition_by=Photo.sighting_id, order_by=(Photo.captured_at.asc(), Photo.id.asc()))
        .label("rn")
    )
    sub = (
        select(Photo.id.label("pid"), rn)
        .where(Photo.sighting_id.in_([r.sighting_id for r in page]), *crit)
        .subquery()
    )
    covers = (
        await session.scalars(
            select(Photo)
            .options(joinedload(Photo.camera).joinedload(Camera.site))
            .where(Photo.id.in_(select(sub.c.pid).where(sub.c.rn == 1)))
        )
    ).all()
    requested = await _outstanding_full_requests(session, covers)
    cover_by_sid = {p.sighting_id: p for p in covers}
    items = []
    for r in page:
        cover = cover_by_sid[r.sighting_id]
        items.append(
            SightingOut(
                id=r.sighting_id,
                camera_id=cover.camera.public_id,
                camera_name=cover.camera.name,
                site_slug=cover.camera.site.slug,
                count=r.count,
                kept_count=r.kept_count or 0,
                started_at=r.started_at,
                ended_at=r.ended_at,
                last_received_at=r.last_received_at,
                cover=_photo_out(cover, requested),
            )
        )
    return SightingPage(
        items=items,
        next_cursor=(
            _encode_cursor(edge.last_received_at, edge.sighting_id) if more and edge else None
        ),
    )


@router.get("/sightings/{sighting_id}/photos", response_model=list[PhotoOut])
async def sighting_photos(sighting_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Every frame of one sighting, chronological — powers the tap-to-expand
    detail pager. Unfiltered on purpose: whatever subset put the sighting on
    screen, opening it means "show me this whole visit". Bounded by the gap
    rule (no pagination; the worst real burst seen is ~170 frames)."""
    photos = (
        await session.scalars(
            select(Photo)
            .options(joinedload(Photo.camera).joinedload(Camera.site))
            .where(Photo.sighting_id == sighting_id)
            .order_by(Photo.captured_at.asc(), Photo.id.asc())
        )
    ).all()
    if not photos:
        raise HTTPException(404, "unknown sighting")
    requested = await _outstanding_full_requests(session, photos)
    return [_photo_out(p, requested) for p in photos]


@router.get("/photos/{photo_ref}", response_model=PhotoOut)
async def get_photo(photo_ref: str, session: AsyncSession = Depends(get_session)):
    """Single photo — powers /photos/<id> deep links in the SPA."""
    photo = await _get_photo(session, photo_ref)
    return _photo_out(photo, await _outstanding_full_requests(session, [photo]))


@router.get("/photos/{photo_ref}/full-request", response_model=FullRequestOut)
async def full_request_status(photo_ref: str, session: AsyncSession = Depends(get_session)):
    """Latest fetch_full command for this photo, with the node's last-seen time —
    powers the overlay's 'where is my HD request' diagnostics."""
    photo = await _get_photo(session, photo_ref)
    cmd = await session.scalar(
        select(Command)
        .where(Command.event_id == photo.event_id, Command.kind == "fetch_full")
        .order_by(Command.created_at.desc(), Command.id.desc())
        .limit(1)
    )
    if cmd is None:
        raise HTTPException(404, "no full-res request for this photo")
    return FullRequestOut(
        status=cmd.status,
        quality=(cmd.payload or {}).get("quality", "standard"),
        requested_by=cmd.requested_by,
        created_at=cmd.created_at,
        delivered_at=cmd.delivered_at,
        received_at=cmd.received_at,
        completed_at=cmd.completed_at,
        detail=cmd.detail,
        node_last_seen_at=photo.camera.last_seen_at,
    )


@router.get("/photos/{photo_ref}/image")
async def photo_image(
    photo_ref: str,
    size: str = "thumb",
    session: AsyncSession = Depends(get_session),
):
    photo = await _get_photo(session, photo_ref)
    if size == "raw":
        # The un-watermarked original — deliberately out of the way (no UI
        # links here): the stamped frame is the product default. Falls back to
        # the stored full for pre-OSD-era photos, which are already pristine.
        if get_settings().demo_mode:
            raise HTTPException(404, "raw originals are not exposed in the demo")
        if photo.full_key is None:
            raise HTTPException(404, "no full image stored")
        body = await get_store().get(raw_variant(photo.full_key))
        if body is not None:
            return Response(
                body,
                media_type=photo.content_type,
                headers={"Cache-Control": "private, max-age=31536000, immutable"},
            )
    key = photo.full_key if size in ("full", "raw") and photo.full_key else photo.thumb_key
    if key is None:
        raise HTTPException(404, "no image stored")
    return StreamingResponse(
        get_store().stream(key),
        media_type=photo.content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.post("/photos/{photo_ref}/keep", response_model=PhotoOut)
async def keep_photo(
    photo_ref: str,
    body: KeepIn,
    session: AsyncSession = Depends(get_session),
):
    photo = await _get_photo(session, photo_ref)
    photo.keep = body.keep
    photo.expires_at = (
        None if body.keep else utcnow() + timedelta(days=get_settings().photo_ttl_days)
    )
    await session.commit()
    return _photo_out(photo, await _outstanding_full_requests(session, [photo]))


@router.post("/photos/{photo_ref}/request-full", response_model=PhotoOut)
async def request_full(
    photo_ref: str,
    body: RequestFullIn | None = None,
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Queue a fetch_full command for the gateway; done automatically when the
    full-res lands via ingest with the same event_id. quality=max is allowed
    even when a (standard) full already exists — the new upload overwrites it."""
    quality = body.quality if body else "standard"
    photo = await _get_photo(session, photo_ref)
    outstanding = await session.scalar(
        select(Command).where(
            Command.event_id == photo.event_id,
            Command.kind == "fetch_full",
            Command.status.in_(OUTSTANDING),
        )
    )
    already_satisfied = photo.full_key is not None and quality == "standard"
    if outstanding is None and not already_satisfied and get_settings().demo_mode:
        # Demo: anonymous visitors share one simulated radio channel — bound
        # the queue so one scripted burst can't pile up hours of transfers.
        queued = await session.scalar(
            select(func.count())
            .select_from(Command)
            .where(Command.kind == "fetch_full", Command.status.in_(OUTSTANDING))
        )
        if queued >= DEMO_MAX_OUTSTANDING:
            raise HTTPException(
                429, "the demo mesh is busy with other transfers — try again in a few minutes"
            )
    if outstanding is None and not already_satisfied:
        cmd = Command(
            camera_id=photo.camera_id,
            kind="fetch_full",
            event_id=photo.event_id,
            payload={"quality": quality},
            requested_by=user["email"],
        )
        session.add(cmd)
        await session.commit()
        publish_request_update(cmd, photo.id, photo.camera.last_seen_at)
        bus.publish(COMMANDS, {})  # wake device command streams
        outstanding = True
    requested = {photo.event_id} if outstanding else set()
    return _photo_out(photo, requested)


# --- Tags ---------------------------------------------------------------------


def _slugify_tag(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:64]


def _publish_photo_updated(photo: Photo, requested: set[str]) -> None:
    """FEED 'photo' update — keeps other open tabs' feeds in sync."""
    bus.publish(
        FEED,
        {
            "event": "photo",
            "data": {**_photo_out(photo, requested).model_dump(mode="json"), "change": "updated"},
        },
    )


@router.get("/tags", response_model=list[TagCountOut])
async def list_tags(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        select(Tag, func.count(photo_tags.c.photo_id))
        .join(photo_tags, photo_tags.c.tag_id == Tag.id, isouter=True)
        .group_by(Tag.id)
        .order_by(Tag.slug)
    )
    return [TagCountOut(slug=t.slug, name=t.name, count=n) for t, n in rows.all()]


@router.post("/photos/{photo_ref}/tags", response_model=PhotoOut)
async def add_photo_tag(
    photo_ref: str, body: TagIn, session: AsyncSession = Depends(get_session)
):
    name = body.name.strip()
    slug = _slugify_tag(name)
    if not slug:
        raise HTTPException(422, "tag name must contain letters or digits")
    photo = await _get_photo(session, photo_ref)
    tag = await session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is None:
        tag = Tag(slug=slug, name=name[:64])
        session.add(tag)
        await session.flush()
    if all(t.id != tag.id for t in photo.tags):
        photo.tags.append(tag)
    await session.commit()
    requested = await _outstanding_full_requests(session, [photo])
    _publish_photo_updated(photo, requested)
    return _photo_out(photo, requested)


@router.delete("/photos/{photo_ref}/tags/{tag_slug}", response_model=PhotoOut)
async def remove_photo_tag(
    photo_ref: str, tag_slug: str, session: AsyncSession = Depends(get_session)
):
    photo = await _get_photo(session, photo_ref)
    tag = next((t for t in photo.tags if t.slug == tag_slug), None)
    if tag is not None:
        photo.tags.remove(tag)
        await session.flush()
        # Drop the tag row once nothing references it — keeps the filter list tidy.
        still_used = await session.scalar(
            select(photo_tags.c.photo_id).where(photo_tags.c.tag_id == tag.id).limit(1)
        )
        if still_used is None:
            await session.delete(tag)
    await session.commit()
    requested = await _outstanding_full_requests(session, [photo])
    _publish_photo_updated(photo, requested)
    return _photo_out(photo, requested)


@router.delete("/photos/{photo_ref}", status_code=204)
async def delete_photo(photo_ref: str, session: AsyncSession = Depends(get_session)):
    photo = await _get_photo(session, photo_ref)
    photo_id = photo.id  # canonical (photo_ref may be a prefix); unreadable post-delete
    sighting_id = photo.sighting_id
    keys = [k for k in (photo.thumb_key, photo.full_key) if k]
    if photo.full_key:
        keys.append(raw_variant(photo.full_key))
    await get_store().delete(keys)
    await session.delete(photo)
    await session.commit()
    bus.publish(
        FEED,
        {
            "event": "photo_removed",
            "data": {"id": str(photo_id), "sighting_id": str(sighting_id)},
        },
    )
    return Response(status_code=204)
