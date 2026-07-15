"""Survey probes: read-side API for the /survey map.

Probes are written by the telemetry ingest (see api/telemetry.py); this
router only reads. Sessions — the "clusters of button presses" the operator
thinks in — are derived on the fly from received_at gaps, never stored, so
they recompute correctly as probes arrive.
"""

import statistics
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from trailcam.auth import current_user
from trailcam.db import get_session
from trailcam.models import Camera, Probe, Site
from trailcam.refs import resolve_uuid_ref
from trailcam.schemas import ProbeOut, ProbeSessionOut, SessionCentroid

router = APIRouter(prefix="/api/v1", tags=["probes"], dependencies=[Depends(current_user)])


async def _filtered(
    session: AsyncSession,
    node: str | None,
    site: str | None,
    from_: datetime | None = None,
    to: datetime | None = None,
) -> Select:
    q = select(Probe).order_by(Probe.received_at, Probe.id)
    if node:
        cam = await resolve_uuid_ref(session, select(Camera), Camera.public_id, node, "node")
        q = q.where(Probe.camera_id == cam.id)
    if site:
        q = q.join(Probe.camera).join(Camera.site).where(Site.slug == site)
    if from_ is not None:
        q = q.where(Probe.received_at >= from_)
    if to is not None:
        q = q.where(Probe.received_at < to)
    return q


def _probe_out(p: Probe) -> ProbeOut:
    return ProbeOut(
        id=p.public_id,
        node_id=p.camera.public_id,
        seq=p.seq,
        kind=p.kind,
        received_at=p.received_at,
        lat=p.lat,
        lon=p.lon,
        alt=p.alt,
        hdop=p.hdop,
        sats=p.sats,
        fix_ok=p.fix_ok,
        profile=p.profile,
        bytes=p.bytes,
        duration_ms=p.duration_ms,
        gw_rssi=p.gw_rssi,
        gw_snr=p.gw_snr,
        leaf_rssi=p.leaf_rssi,
        leaf_snr=p.leaf_snr,
    )


@router.get("/probes", response_model=list[ProbeOut])
async def list_probes(
    node: str | None = None,
    site: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    # `only` hides the fiction rows (no GPS fix) by default; the survey UI
    # asks for `all` and keeps the no-fix probes in a count + side list.
    fix: Literal["only", "all"] = "only",
    session: AsyncSession = Depends(get_session),
):
    q = (await _filtered(session, node, site, from_, to)).options(joinedload(Probe.camera))
    if fix == "only":
        q = q.where(Probe.fix_ok.is_(True))
    return [_probe_out(p) for p in (await session.scalars(q)).all()]


@router.get("/probes/sessions", response_model=list[ProbeSessionOut])
async def probe_sessions(
    node: str | None = None,
    site: str | None = None,
    # A gap longer than this starts a new session. That is the whole
    # algorithm — it reproduces "clusters of button presses" exactly.
    gap_min: int = Query(default=30, ge=1, le=24 * 60),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.scalars(await _filtered(session, node, site))).all()
    gap_s = gap_min * 60

    sessions: list[list[Probe]] = []
    for p in rows:
        if (
            not sessions
            or (p.received_at - sessions[-1][-1].received_at).total_seconds() > gap_s
        ):
            sessions.append([])
        sessions[-1].append(p)

    out: list[ProbeSessionOut] = []
    for i, group in enumerate(sessions):
        # Medians and centroids only mean anything on real measurements:
        # rank by gw_rssi (the clean instrument) over probes that have one,
        # centroid over probes whose coordinates aren't fiction.
        rssi = [p.gw_rssi for p in group if p.gw_rssi is not None]
        fixed = [p for p in group if p.fix_ok]
        out.append(
            ProbeSessionOut(
                index=i,
                started_at=group[0].received_at,
                ended_at=group[-1].received_at,
                count=len(group),
                fix_count=len(fixed),
                median_gw_rssi=statistics.median(rssi) if rssi else None,
                centroid=(
                    SessionCentroid(
                        lat=sum(p.lat for p in fixed) / len(fixed),
                        lon=sum(p.lon for p in fixed) / len(fixed),
                    )
                    if fixed
                    else None
                ),
            )
        )
    return out
