from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from trailcam.auth import current_user
from trailcam.config import get_settings
from trailcam.db import get_session
from trailcam.models import Camera, CameraAlias, Command, Photo, Probe, Site, Telemetry
from trailcam.refs import resolve_uuid_ref, uuid_ref_bounds
from trailcam.schemas import (
    CameraAliasOut,
    CameraMergeIn,
    CameraMergeOut,
    CameraOut,
    CameraPatch,
    MeOut,
    SiteOut,
    SitePatch,
)
from trailcam.sightings import regroup_sightings

router = APIRouter(prefix="/api/v1", tags=["cameras"], dependencies=[Depends(current_user)])


@router.get("/me", response_model=MeOut)
async def me(user: dict = Depends(current_user)):
    # demo reflects the instance config, not the user dict — so a real family
    # member browsing the demo instance still sees the read-only UI.
    s = get_settings()
    return MeOut(
        email=user["email"],
        name=user["name"],
        demo=s.demo_mode,
        map_tile_url=s.map_tile_url,
        map_tile_attribution=s.map_tile_attribution,
    )


def _site_out(s: Site) -> SiteOut:
    return SiteOut(id=s.public_id, slug=s.slug, name=s.name, hidden=s.hidden)


@router.get("/sites", response_model=list[SiteOut])
async def list_sites(include_hidden: bool = False, session: AsyncSession = Depends(get_session)):
    q = select(Site).order_by(Site.slug)
    if not include_hidden:
        q = q.where(Site.hidden.is_(False))
    return [_site_out(s) for s in (await session.scalars(q)).all()]


@router.patch("/sites/{site_ref}", response_model=SiteOut)
async def patch_site(site_ref: str, body: SitePatch, session: AsyncSession = Depends(get_session)):
    site = await resolve_uuid_ref(session, select(Site), Site.public_id, site_ref, "site")
    if body.name is not None:
        site.name = body.name
    if body.hidden is not None:
        site.hidden = body.hidden
    await session.commit()
    return _site_out(site)


def _camera_out(c: Camera, aliases: list[str] | None = None) -> CameraOut:
    return CameraOut(
        id=c.public_id,
        slug=c.slug,
        name=c.name,
        kind=c.kind,
        site_slug=c.site.slug,
        notes=c.notes,
        hidden=c.hidden,
        last_seen_at=c.last_seen_at,
        last_battery_v=c.last_battery_v,
        lat=c.lat,
        lon=c.lon,
        aliases=aliases if aliases is not None else sorted(a.slug for a in c.aliases),
    )


@router.get("/cameras", response_model=list[CameraOut])
async def list_cameras(
    site: str | None = None,
    include_hidden: bool = False,
    session: AsyncSession = Depends(get_session),
):
    q = (
        select(Camera)
        .options(joinedload(Camera.site), selectinload(Camera.aliases))
        .order_by(Camera.name)
    )
    if site:
        q = q.join(Camera.site).where(Site.slug == site)
    if not include_hidden:
        q = q.where(Camera.hidden.is_(False))
    return [_camera_out(c) for c in (await session.scalars(q)).all()]


@router.patch("/cameras/{camera_ref}", response_model=CameraOut)
async def patch_camera(
    camera_ref: str, body: CameraPatch, session: AsyncSession = Depends(get_session)
):
    cam = await resolve_uuid_ref(
        session,
        select(Camera).options(joinedload(Camera.site), selectinload(Camera.aliases)),
        Camera.public_id,
        camera_ref,
        "camera",
    )
    if body.name is not None:
        cam.name = body.name
    if body.notes is not None:
        cam.notes = body.notes
    if body.hidden is not None:
        cam.hidden = body.hidden
    # lat/lon land together (the survey map's gateway pin-drop) — a position
    # is a pair, patching one axis alone would be a bug on the caller's side.
    if body.lat is not None and body.lon is not None:
        cam.lat = body.lat
        cam.lon = body.lon
    await session.commit()
    return _camera_out(cam)


async def _resolve_node(session: AsyncSession, site_id: int, ref: str) -> Camera:
    """A camera by UUID ref, or by bare slug within `site_id`.

    Slug is accepted because it is the id a human has after a node
    re-identifies itself: the boot banner, the gateway log and the announce
    tail all speak slugs, and nobody has the new row's UUID to hand."""
    if uuid_ref_bounds(ref) is not None:
        return await resolve_uuid_ref(
            session,
            select(Camera).options(joinedload(Camera.site), selectinload(Camera.aliases)),
            Camera.public_id,
            ref,
            "camera",
        )
    cam = await session.scalar(
        select(Camera)
        .options(joinedload(Camera.site), selectinload(Camera.aliases))
        .where(Camera.site_id == site_id, Camera.slug == ref)
    )
    if cam is None:
        raise HTTPException(404, "camera not found")
    return cam


@router.post("/cameras/{camera_ref}/merge", response_model=CameraMergeOut)
async def merge_camera(
    camera_ref: str, body: CameraMergeIn, session: AsyncSession = Depends(get_session)
):
    """Fold a duplicate node row into this one, and keep answering to its slug.

    The duplicate exists because a leaf's wire slug is derived from its RNS
    identity, and that identity can be lost — a full `esptool write-flash 0x0`
    writes 0xFF across the nvs partition, and arduino-esp32 erases nvs of its
    own accord when it fills. The board reappears under a new slug, ingest
    auto-creates a second camera, and one physical node ends up owning two
    rows with half its history each.

    This moves the source row's photos, telemetry, probes and commands onto the
    kept camera, regroups the kept camera's sightings (bursts that straddled
    the split were never one group), records the source slug as an alias so the
    device needs no reflash, and deletes the source row. The alias is what
    makes it stick: the node keeps announcing under its new identity, so
    without it the next heartbeat re-creates exactly what was just merged.
    """
    cam = await resolve_uuid_ref(
        session,
        select(Camera).options(joinedload(Camera.site), selectinload(Camera.aliases)),
        Camera.public_id,
        camera_ref,
        "camera",
    )
    src = await _resolve_node(session, cam.site_id, body.source)
    if src.id == cam.id:
        raise HTTPException(409, "a camera cannot be merged into itself")
    if src.site_id != cam.site_id:
        # Slugs are unique per site and a re-identified board does not move
        # sites on its own, so a cross-site merge is far more likely a typo
        # than an intent — and it is the one mistake here that silently
        # relocates a whole node's history.
        raise HTTPException(409, "cameras are in different sites")

    moved = {}
    for label, model in (
        ("photos", Photo),
        ("telemetry", Telemetry),
        ("probes", Probe),
        ("commands", Command),
    ):
        moved[label] = await session.scalar(
            select(func.count()).select_from(model).where(model.camera_id == src.id)
        )
        await session.execute(
            update(model).where(model.camera_id == src.id).values(camera_id=cam.id)
        )

    # Aliases pointing at the row about to disappear have to follow it, or the
    # cascade would delete them and an earlier merge would quietly come undone.
    await session.execute(
        update(CameraAlias).where(CameraAlias.camera_id == src.id).values(camera_id=cam.id)
    )

    merged_slug = src.slug
    await session.delete(src)
    await session.flush()

    aliased = False
    if body.alias:
        session.add(
            CameraAlias(site_id=cam.site_id, slug=merged_slug, camera_id=cam.id, note=body.note)
        )
        aliased = True

    sightings = await regroup_sightings(session, cam.id, get_settings().sighting_gap_min)

    # last_seen_at / last_battery_v are denormalized from telemetry, so the
    # kept row must not claim it has been quiet since before the merge.
    if src.last_seen_at and (cam.last_seen_at is None or src.last_seen_at > cam.last_seen_at):
        cam.last_seen_at = src.last_seen_at
        cam.last_battery_v = src.last_battery_v

    await session.commit()
    await session.refresh(cam, ["site", "aliases"])
    return CameraMergeOut(
        camera=_camera_out(cam),
        merged_slug=merged_slug,
        aliased=aliased,
        sightings=sightings,
        **moved,
    )


@router.get("/cameras/{camera_ref}/aliases", response_model=list[CameraAliasOut])
async def list_aliases(camera_ref: str, session: AsyncSession = Depends(get_session)):
    cam = await resolve_uuid_ref(
        session,
        select(Camera).options(selectinload(Camera.aliases)),
        Camera.public_id,
        camera_ref,
        "camera",
    )
    return [
        CameraAliasOut(slug=a.slug, created_at=a.created_at, note=a.note)
        for a in sorted(cam.aliases, key=lambda a: a.slug)
    ]


@router.delete("/cameras/{camera_ref}/aliases/{slug}", status_code=204)
async def delete_alias(camera_ref: str, slug: str, session: AsyncSession = Depends(get_session)):
    """Stop answering to a retired slug.

    The merge itself is not undone — the moved rows stay moved. What comes
    back is auto-create: the next payload under this slug makes a fresh camera
    again. That is the escape hatch for a merge aimed at the wrong node."""
    cam = await resolve_uuid_ref(session, select(Camera), Camera.public_id, camera_ref, "camera")
    result = await session.execute(
        delete(CameraAlias).where(CameraAlias.camera_id == cam.id, CameraAlias.slug == slug)
    )
    if result.rowcount == 0:
        raise HTTPException(404, "alias not found")
    await session.commit()
