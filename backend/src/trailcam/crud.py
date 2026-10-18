"""Shared node lookup/auto-create — used by both ingest and telemetry, which is
what lets the mesh 'add itself': the first payload from a new site/node slug
creates the rows."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.models import Camera, CameraAlias, Site


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
    if node is not None:
        if node.last_wire_slug != node_slug:
            node.last_wire_slug = node_slug
        return node
    # No camera under that slug — but it may be a slug we have already merged
    # away. A leaf that lost its RNS identity announces under a new name
    # forever, so without this check every merge would be undone by
    # the node's next heartbeat, re-creating the duplicate row a human just
    # cleaned up. Checked only on the miss path, so a healthy node pays nothing.
    aliased = await session.scalar(
        select(Camera)
        .join(CameraAlias, CameraAlias.camera_id == Camera.id)
        .where(CameraAlias.site_id == site.id, CameraAlias.slug == node_slug)
    )
    if aliased is not None:
        # Remember which name it used. Commands go back out addressed by slug, and
        # this is the only record of the one the board still answers to.
        if aliased.last_wire_slug != node_slug:
            aliased.last_wire_slug = node_slug
        return aliased
    node = Camera(
        site_id=site.id, slug=node_slug, name=node_slug.upper(), kind=kind, last_wire_slug=node_slug
    )
    session.add(node)
    await session.flush()
    return node
