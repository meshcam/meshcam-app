"""Node telemetry: device-token heartbeats in, session-auth health views out.

POST /api/v1/telemetry is the mesh-side contract (gateway forwards node
beacons); /api/v1/nodes/* feeds the frontend health page."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from trailcam.auth import current_device, current_user
from trailcam.crud import get_or_create_node
from trailcam.db import get_session
from trailcam.events import COMMANDS, FEED, bus
from trailcam.models import Camera, Command, Photo, Telemetry, utcnow
from trailcam.probes import probe_from_extra
from trailcam.refs import resolve_uuid_ref
from trailcam.schemas import (
    MeshEvent,
    NodeCommandIn,
    NodeCommandOut,
    NodeHealth,
    NodeHealthCounters,
    TelemetryAck,
    TelemetryIn,
    TelemetryPoint,
    TelemetrySeries,
    TelemetrySnapshot,
)

ingest_router = APIRouter(prefix="/api/v1", tags=["telemetry"])
nodes_router = APIRouter(prefix="/api/v1", tags=["nodes"], dependencies=[Depends(current_user)])

SERIES_MAX_POINTS = 2000


@ingest_router.post(
    "/telemetry", response_model=TelemetryAck, dependencies=[Depends(current_device)]
)
async def post_telemetry(body: TelemetryIn, session: AsyncSession = Depends(get_session)):
    node = await get_or_create_node(session, body.site, body.node, kind=body.kind or "camera")
    if body.kind and node.kind != body.kind:
        node.kind = body.kind
    row = Telemetry(
        camera_id=node.id,
        reported_at=body.reported_at,
        battery_v=body.battery_v,
        temp_c=body.temp_c,
        pressure_hpa=body.pressure_hpa,
        rssi=body.rssi,
        snr=body.snr,
        uptime_s=body.uptime_s,
        boot_reason=body.boot_reason,
        fw_version=body.fw_version,
        extra=body.extra,
    )
    session.add(row)
    # Survey probes ride the same gateway post (extra["probe"]) but are
    # permanent records, not disposable health data — mirror them into the
    # probes table, which the retention purges never touch. The telemetry row
    # stays too (the live mesh feed reads it) and keeps aging out.
    if body.extra:
        probe = probe_from_extra(node.id, body.extra, gw_rssi=body.rssi, gw_snr=body.snr)
        if probe is not None:
            session.add(probe)
    node.last_seen_at = utcnow()
    if body.battery_v is not None:
        node.last_battery_v = body.battery_v
    await session.commit()
    bus.publish(FEED, {"event": "node", "data": {"node_id": str(node.public_id)}})
    # Mesh live feed: unlike "node" (a bare refetch hint), this carries the beat
    # itself so the traffic panel can render without a round trip per event.
    bus.publish(
        FEED,
        {
            "event": "mesh",
            "data": MeshEvent(
                at=row.received_at or utcnow(),
                site=body.site,
                node=body.node,
                kind=node.kind,
                rssi=body.rssi,
                snr=body.snr,
                battery_v=body.battery_v,
                fw_version=body.fw_version,
                extra=body.extra,
            ).model_dump(mode="json"),
        },
    )
    return TelemetryAck(id=row.id, node_id=node.id)


@nodes_router.get("/mesh/recent", response_model=list[MeshEvent])
async def mesh_recent(
    limit: int = Query(default=80, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """Initial fill for the live mesh-traffic panel: newest telemetry first."""
    rows = (
        await session.scalars(
            select(Telemetry)
            .options(joinedload(Telemetry.camera).joinedload(Camera.site))
            .order_by(Telemetry.received_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        MeshEvent(
            at=t.received_at,
            site=t.camera.site.slug,
            node=t.camera.slug,
            kind=t.camera.kind,
            rssi=t.rssi,
            snr=t.snr,
            battery_v=t.battery_v,
            fw_version=t.fw_version,
            extra=t.extra,
        )
        for t in rows
    ]


@nodes_router.get("/nodes/health", response_model=list[NodeHealth])
async def nodes_health(session: AsyncSession = Depends(get_session)):
    q = select(Camera).options(joinedload(Camera.site)).order_by(Camera.name)
    nodes = (await session.scalars(q)).all()
    ids = [n.id for n in nodes]

    latest: dict[int, Telemetry] = {}
    # Leaf health counters (bug 5): extra.health = since-boot {pir_wakes, captures,
    # push_fails} from every announce. Keep the newest per node, plus the minimum
    # push_fails seen in the recent window — latest > min means pushes are actively
    # failing (a reboot resets the counters to 0, so the min self-adjusts).
    health_window = utcnow() - timedelta(hours=6)
    latest_health: dict[int, dict] = {}
    min_push_fails: dict[int, int] = {}
    if ids:
        rows = await session.scalars(
            select(Telemetry)
            .where(Telemetry.camera_id.in_(ids))
            .order_by(Telemetry.camera_id, Telemetry.received_at.desc())
        )
        for t in rows:
            latest.setdefault(t.camera_id, t)
            h = (t.extra or {}).get("health")
            if isinstance(h, dict) and t.received_at >= health_window:
                latest_health.setdefault(t.camera_id, h)
                pf = h.get("push_fails")
                if isinstance(pf, int) and pf >= 0:
                    cur = min_push_fails.get(t.camera_id)
                    min_push_fails[t.camera_id] = pf if cur is None else min(cur, pf)

    last_photo: dict[int, object] = {}
    if ids:
        rows = await session.execute(
            select(Photo.camera_id, func.max(Photo.captured_at))
            .where(Photo.camera_id.in_(ids))
            .group_by(Photo.camera_id)
        )
        last_photo = dict(rows.all())

    def _push_failing(cam_id: int) -> bool:
        h = latest_health.get(cam_id)
        if not h or not isinstance(h.get("push_fails"), int):
            return False
        return h["push_fails"] > min_push_fails.get(cam_id, h["push_fails"])

    return [
        NodeHealth(
            id=n.public_id,
            slug=n.slug,
            name=n.name,
            kind=n.kind,
            site_slug=n.site.slug,
            last_seen_at=n.last_seen_at,
            last_battery_v=n.last_battery_v,
            last_photo_at=last_photo.get(n.id),
            latest=(
                TelemetrySnapshot.model_validate(latest[n.id]) if n.id in latest else None
            ),
            health=(
                NodeHealthCounters.model_validate(latest_health[n.id])
                if n.id in latest_health
                else None
            ),
            push_failing=_push_failing(n.id),
        )
        for n in nodes
    ]


@nodes_router.post("/nodes/{node_ref}/commands", status_code=202)
async def queue_node_command(
    node_ref: str,
    body: NodeCommandIn,
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Operator downlink beyond photo fetches: queue a node-level command for the
    gateway to deliver on the node's next announce. maintenance = stay awake with
    both command channels open (payload {ssid, psk, minutes}); sleep = end an
    active maintenance window early."""
    node = await resolve_uuid_ref(session, select(Camera), Camera.public_id, node_ref, "node")
    cmd = Command(
        camera_id=node.id,
        kind=body.kind,
        payload=body.payload,
        requested_by=user["email"],
    )
    session.add(cmd)
    await session.commit()
    bus.publish(COMMANDS, {})  # wake device command streams
    return {"id": cmd.id, "kind": cmd.kind, "status": cmd.status}


SENSITIVE_PAYLOAD_KEYS = ("psk",)


def _sanitized_payload(payload: dict | None) -> dict | None:
    """Command payloads can carry WiFi creds — never echo secrets back out."""
    if not payload:
        return payload
    return {k: ("••••" if k in SENSITIVE_PAYLOAD_KEYS else v) for k, v in payload.items()}


@nodes_router.get("/nodes/{node_ref}/commands", response_model=list[NodeCommandOut])
async def node_commands(
    node_ref: str,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    node = await resolve_uuid_ref(session, select(Camera), Camera.public_id, node_ref, "node")
    rows = (
        await session.scalars(
            select(Command)
            .where(Command.camera_id == node.id)
            .order_by(Command.created_at.desc(), Command.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        NodeCommandOut(
            id=c.id,
            kind=c.kind,
            status=c.status,
            payload=_sanitized_payload(c.payload),
            event_id=c.event_id,
            requested_by=c.requested_by,
            detail=c.detail,
            created_at=c.created_at,
            delivered_at=c.delivered_at,
            received_at=c.received_at,
            completed_at=c.completed_at,
        )
        for c in rows
    ]


@nodes_router.get("/nodes/{node_ref}/telemetry", response_model=TelemetrySeries)
async def node_telemetry(
    node_ref: str,
    hours: int = Query(default=168, ge=1, le=24 * 365),
    session: AsyncSession = Depends(get_session),
):
    node = await resolve_uuid_ref(session, select(Camera), Camera.public_id, node_ref, "node")
    since = utcnow() - timedelta(hours=hours)
    rows = (
        await session.scalars(
            select(Telemetry)
            .where(Telemetry.camera_id == node.id, Telemetry.received_at >= since)
            .order_by(Telemetry.received_at)
            .limit(SERIES_MAX_POINTS)
        )
    ).all()
    return TelemetrySeries(points=[TelemetryPoint.model_validate(t) for t in rows])
