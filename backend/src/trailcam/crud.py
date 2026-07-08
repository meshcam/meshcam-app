"""Shared node lookup/auto-create — used by both ingest and telemetry, which is
what lets the mesh 'add itself': the first payload from a new site/node slug
creates the rows."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.models import Camera, Site


async def get_or_create_node(
    session: AsyncSession, site_slug: str, node_slug: str, kind: str = "camera"
) -> Camera:
    site = await session.scalar(select(Site).where(Site.slug == site_slug))
    if site is None:
        site = Site(slug=site_slug, name=site_slug.replace("-", " ").title())
        session.add(site)
        await session.flush()
    node = await session.scalar(
        select(Camera).where(Camera.site_id == site.id, Camera.slug == node_slug)
    )
    if node is None:
        node = Camera(site_id=site.id, slug=node_slug, name=node_slug.upper(), kind=kind)
        session.add(node)
        await session.flush()
    return node
