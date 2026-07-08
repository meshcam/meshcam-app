from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from trailcam.auth import current_user
from trailcam.config import get_settings
from trailcam.db import get_session
from trailcam.models import Camera, Site
from trailcam.refs import resolve_uuid_ref
from trailcam.schemas import CameraOut, CameraPatch, MeOut, SiteOut, SitePatch

router = APIRouter(prefix="/api/v1", tags=["cameras"], dependencies=[Depends(current_user)])


@router.get("/me", response_model=MeOut)
async def me(user: dict = Depends(current_user)):
    # demo reflects the instance config, not the user dict — so a real family
    # member browsing the demo instance still sees the read-only UI.
    return MeOut(email=user["email"], name=user["name"], demo=get_settings().demo_mode)


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


def _camera_out(c: Camera) -> CameraOut:
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
    )


@router.get("/cameras", response_model=list[CameraOut])
async def list_cameras(
    site: str | None = None,
    include_hidden: bool = False,
    session: AsyncSession = Depends(get_session),
):
    q = select(Camera).options(joinedload(Camera.site)).order_by(Camera.name)
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
        select(Camera).options(joinedload(Camera.site)),
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
    await session.commit()
    return _camera_out(cam)
