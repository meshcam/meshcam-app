import base64
import re
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, tuple_
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
    KeepIn,
    PhotoOut,
    PhotoPage,
    RequestFullIn,
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


def _encode_cursor(p: Photo) -> str:
    return base64.urlsafe_b64encode(f"{p.received_at.isoformat()}|{p.id}".encode()).decode()


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


@router.get("/photos", response_model=PhotoPage)
async def list_photos(
    site: str | None = None,
    camera_id: str | None = None,
    kept: bool | None = None,
    tag: str | None = None,
    captured_after: datetime | None = None,
    captured_before: datetime | None = None,
    before: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    # Feed order = arrival order (received_at): leaf clocks drift/freeze (no NTP
    # in the woods) and store-and-forward delivers late — the server clock is
    # the only monotonic truth. captured_at is display metadata — but it IS what
    # the captured_after/before window filters on, because "what moved Tuesday
    # night" is a question about capture time, drift and all.
    limit = min(max(limit, 1), 200)
    q = (
        select(Photo)
        .options(joinedload(Photo.camera).joinedload(Camera.site))
        .order_by(Photo.received_at.desc(), Photo.id.desc())
        .limit(limit + 1)
    )
    if site:
        q = q.join(Photo.camera).join(Camera.site).where(Site.slug == site)
    if camera_id is not None:
        cam = await resolve_uuid_ref(session, select(Camera), Camera.public_id, camera_id, "camera")
        q = q.where(Photo.camera_id == cam.id)
    if kept:
        q = q.where(Photo.keep.is_(True))
    if tag:
        q = q.join(photo_tags, photo_tags.c.photo_id == Photo.id).join(
            Tag, Tag.id == photo_tags.c.tag_id
        ).where(Tag.slug == tag)
    if captured_after is not None:
        q = q.where(Photo.captured_at >= captured_after)
    if captured_before is not None:
        q = q.where(Photo.captured_at < captured_before)
    if before:
        ts, pid = _decode_cursor(before)
        q = q.where(tuple_(Photo.received_at, Photo.id) < (ts, pid))
    rows = (await session.scalars(q)).all()
    page, more = rows[:limit], len(rows) > limit
    requested = await _outstanding_full_requests(session, page)
    return PhotoPage(
        items=[_photo_out(p, requested) for p in page],
        next_cursor=_encode_cursor(page[-1]) if more and page else None,
    )


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
    keys = [k for k in (photo.thumb_key, photo.full_key) if k]
    if photo.full_key:
        keys.append(raw_variant(photo.full_key))
    await get_store().delete(keys)
    await session.delete(photo)
    await session.commit()
    bus.publish(FEED, {"event": "photo_removed", "data": {"id": str(photo_id)}})
    return Response(status_code=204)
