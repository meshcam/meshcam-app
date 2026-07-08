"""Ingest endpoint for the mesh gateways.

The mesh gateway POSTs each capture event here
over the WireGuard tunnel. Thumbnail first (~5 KB over LoRa), full-res later if
requested — both carry the same event_id and land on one Photo row, so the
endpoint is idempotent per (event_id, kind)."""

import json
import logging
from datetime import UTC, datetime, timedelta
from functools import partial

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam import osd
from trailcam.auth import current_device
from trailcam.config import get_settings
from trailcam.crud import get_or_create_node
from trailcam.db import get_session
from trailcam.events import FEED, bus, publish_request_update
from trailcam.models import OUTSTANDING, Command, Photo, Site, utcnow
from trailcam.s3 import get_store, raw_variant
from trailcam.schemas import IngestOut, PhotoOut, SiteOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["ingest"])

CLOCK_SLACK = timedelta(minutes=5)


@router.post("/ingest", response_model=IngestOut, dependencies=[Depends(current_device)])
async def ingest(
    file: UploadFile,
    site: str = Form(...),
    camera: str = Form(...),
    event_id: str = Form(...),
    captured_at: datetime = Form(...),
    kind: str = Form("thumb"),
    meta: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    if kind not in ("thumb", "full"):
        raise HTTPException(422, "kind must be thumb or full")
    try:
        meta_dict = json.loads(meta) if meta else None
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "meta must be JSON") from exc

    body = await file.read()
    if not body:
        raise HTTPException(422, "empty file")
    content_type = file.content_type or "image/jpeg"

    # Leaf clocks are untrustworthy (no NTP, deep-sleep drift — observed hours
    # fast on the bench). Naive timestamps are assumed UTC; future timestamps
    # are clamped to server time, keeping the node's claim in meta.
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    now = utcnow()
    if captured_at > now + CLOCK_SLACK:
        meta_dict = {**(meta_dict or {}), "reported_captured_at": captured_at.isoformat()}
        captured_at = now

    cam = await get_or_create_node(session, site, camera)
    photo = await session.scalar(select(Photo).where(Photo.event_id == event_id))
    created = photo is None
    if photo is None:
        settings = get_settings()
        photo = Photo(
            event_id=event_id,
            camera_id=cam.id,
            captured_at=captured_at,
            content_type=content_type,
            meta=meta_dict,
            expires_at=utcnow() + timedelta(days=settings.photo_ttl_days),
        )
        session.add(photo)
        await session.flush()
    elif meta_dict:
        photo.meta = {**(photo.meta or {}), **meta_dict}

    key = f"{site}/{camera}/{captured_at:%Y/%m}/{event_id}.{kind}.jpg"
    settings = get_settings()
    if settings.osd_stamp and content_type == "image/jpeg":
        # The watermark is the product default on EVERY image — a deep node's
        # thumbnail may be the only frame that ever arrives, and the stamped
        # frame is what people share. Fulls keep their pristine bytes at a
        # .raw sibling key (ops/API-only, ?size=raw); thumbs are stamped in
        # place. Temp/battery ride in from the capture's meta; a frame that
        # won't decode is stored untouched — never lose an image to a
        # watermark.
        info = photo.meta or {}

        def _num(v: object) -> float | None:
            return float(v) if isinstance(v, int | float) else None

        try:
            stamped = await anyio.to_thread.run_sync(
                partial(
                    osd.stamp,
                    body,
                    camera_label=cam.name,
                    captured_at=photo.captured_at,
                    temp_c=_num(info.get("temp_c")),
                    battery_v=_num(info.get("battery_v")),
                    tz=settings.osd_tz,
                )
            )
        except Exception:
            logger.warning("osd stamp failed for %s — storing unstamped", event_id, exc_info=True)
        else:
            if kind == "full":
                await get_store().put(raw_variant(key), body, content_type)
            body = stamped
    await get_store().put(key, body, content_type)
    if kind == "thumb":
        photo.thumb_key, photo.thumb_size = key, len(body)
    else:
        photo.full_key, photo.full_size = key, len(body)
        # a full-res arrival satisfies any outstanding fetch_full command
        await session.execute(
            update(Command)
            .where(
                Command.event_id == event_id,
                Command.kind == "fetch_full",
                Command.status.in_(OUTSTANDING),
            )
            .values(status="done", completed_at=utcnow())
        )

    cam.last_seen_at = utcnow()
    battery = (meta_dict or {}).get("battery_v")
    if isinstance(battery, int | float):
        cam.last_battery_v = float(battery)

    await session.commit()

    # Live UI: push the fresh photo to every connected browser. full_requested
    # is false by construction here (a new photo can't have requests yet; a
    # full arrival just completed any outstanding one).
    bus.publish(
        FEED,
        {
            "event": "photo",
            "data": {
                **PhotoOut(
                    id=photo.id,
                    event_id=event_id,
                    camera_id=cam.public_id,
                    camera_name=cam.name,
                    site_slug=site,
                    captured_at=photo.captured_at,
                    received_at=photo.received_at,
                    keep=photo.keep,
                    expires_at=photo.expires_at,
                    has_full=photo.full_key is not None,
                    full_requested=False,
                    thumb_size=photo.thumb_size,
                    full_size=photo.full_size,
                    meta=photo.meta,
                ).model_dump(mode="json"),
                "change": "new" if created else "updated",
            },
        },
    )
    if kind == "full":
        # request diagnostics: the fetch_full (if any) just completed
        cmd = await session.scalar(
            select(Command)
            .where(Command.event_id == event_id, Command.kind == "fetch_full")
            .order_by(Command.created_at.desc(), Command.id.desc())
            .limit(1)
        )
        if cmd is not None:
            publish_request_update(cmd, photo.id, cam.last_seen_at)
    return IngestOut(id=photo.id, event_id=event_id, stored=kind)


@router.get("/site", response_model=SiteOut, dependencies=[Depends(current_device)])
async def device_site(slug: str, session: AsyncSession = Depends(get_session)):
    """A gateway reads its own site's display name here so its OLED can show the
    address, and a rename in the settings UI trickles down to the screen. The
    slug is the immutable ingest key; name is the renameable display value.
    Created on first read (same "the mesh adds itself" rule as ingest) so a new
    gateway's site is immediately renameable, before its first photo lands."""
    site = await session.scalar(select(Site).where(Site.slug == slug))
    if site is None:
        site = Site(slug=slug, name=slug.replace("-", " ").title())
        session.add(site)
        await session.commit()
        await session.refresh(site)
    return SiteOut(id=site.public_id, slug=site.slug, name=site.name, hidden=site.hidden)
