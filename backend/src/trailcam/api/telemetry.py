"""Node telemetry: device-token heartbeats in, session-auth health views out.

POST /api/v1/telemetry is the mesh-side contract (gateway forwards node
beacons); /api/v1/nodes/* feeds the frontend health page."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from trailcam.auth import current_device, current_user
from trailcam.crud import get_or_create_node
from trailcam.db import get_session
from trailcam.events import COMMANDS, FEED, bus
from trailcam.identity import prev_node_from_extra, unmerged_prev_nodes
from trailcam.models import Camera, Command, Photo, Telemetry, utcnow
from trailcam.probes import probe_from_extra
from trailcam.refs import resolve_uuid_ref
from trailcam.schemas import (
    STORAGE_STATE_BY_CODE,
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


# "Newest" for a node is received_at desc with id desc breaking the tie, so a
# gateway draining a spool of beats into the same instant still has one answer.
NEWEST_FIRST = (Telemetry.received_at.desc(), Telemetry.id.desc())

HEALTH_WINDOW = timedelta(hours=6)

# The key test for a re-identification beat, in SQL. JSON on SQLite, JSONB on
# Postgres, both answer it. Named because migration 0014 has to spell the same
# predicate BY HAND to build the partial index that serves it: Postgres will
# only use a partial index when the query's predicate matches the index's, and
# it proves that by comparing expression trees, not meaning. It will not, for
# instance, prove `extra ->> 'prev_node' IS NOT NULL` from `extra ? 'prev_node'`
# — measured on a real Postgres. So the compiled text of THIS expression is
# load-bearing DDL, and test_node_health_query pins it against the migration.
PREV_NODE_PRESENT = Telemetry.extra["prev_node"].as_string().is_not(None)


async def _latest_telemetry(session: AsyncSession, ids: list[int]) -> dict[int, Telemetry]:
    """The newest telemetry row per node — one hydrated row each, not the table.

    ROW_NUMBER over camera_id picks the winners inside the database and only
    their ids come back; those rows are then loaded as ORM objects because the
    snapshot renders every column. This endpoint used to stream and hydrate
    every telemetry row ever written, on every poll, to keep the first per node
    — and the nodes view refetches on a 2 s debounce off every live
    'node' event."""
    if not ids:
        return {}
    ranked = (
        select(
            Telemetry.id.label("id"),
            func.row_number()
            .over(partition_by=Telemetry.camera_id, order_by=NEWEST_FIRST)
            .label("rn"),
        )
        .where(Telemetry.camera_id.in_(ids))
        .subquery()
    )
    newest = select(ranked.c.id).where(ranked.c.rn == 1)
    rows = await session.scalars(select(Telemetry).where(Telemetry.id.in_(newest)))
    return {t.camera_id: t for t in rows}


async def _windowed_extras(
    session: AsyncSession, ids: list[int], since: datetime
) -> tuple[dict[int, dict], dict[int, str], dict[int, int]]:
    """The three things read out of `extra` within the recency window, in one pass.

    Bounded two ways against the old full-table scan: the window is now a SQL
    predicate on the indexed (camera_id, received_at) pair rather than a Python
    test on every row ever written, and only the two columns the rules read
    come back — no ORM hydration. Newest-first ordering is what makes the
    setdefaults below mean "the newest row that carried one". Comparing in SQL
    also retires the naive/aware normalisation the old loop needed per row:
    `received_at` is written UTC everywhere (see purge.py, which bounds the
    same column the same way).

    Leaf health counters (bug 5): extra.health = since-boot {pir_wakes, captures,
    push_fails} from every announce. Keep the newest per node, plus the minimum
    push_fails seen in the window — latest > min means pushes are actively
    failing (a reboot resets the counters to 0, so the min self-adjusts).

    Store state: the gateway forwards the leaf's sd= flag as extra.sd,
    and the flag is only present when the store is degraded. Take it from the
    newest row that carries one, within the same window as the counters — a
    stale "unreadable" from days ago should not keep offering a format."""
    latest_health: dict[int, dict] = {}
    latest_storage: dict[int, str] = {}
    min_push_fails: dict[int, int] = {}
    if not ids:
        return latest_health, latest_storage, min_push_fails
    rows = await session.execute(
        select(Telemetry.camera_id, Telemetry.extra)
        .where(Telemetry.camera_id.in_(ids), Telemetry.received_at >= since)
        .order_by(Telemetry.camera_id, *NEWEST_FIRST)
    )
    for cam_id, extra in rows.all():
        extra = extra or {}
        if not isinstance(extra, dict):
            continue  # `extra` is whatever the gateway posted
        sd = extra.get("sd")
        if isinstance(sd, str):
            latest_storage.setdefault(cam_id, STORAGE_STATE_BY_CODE.get(sd, "unknown"))
        h = extra.get("health")
        if isinstance(h, dict):
            latest_health.setdefault(cam_id, h)
            pf = h.get("push_fails")
            if isinstance(pf, int) and pf >= 0:
                cur = min_push_fails.get(cam_id)
                min_push_fails[cam_id] = pf if cur is None else min(cur, pf)
    return latest_health, latest_storage, min_push_fails


async def _reported_prev_nodes(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    """Re-identification: the newest beat per node that CARRIED
    extra.prev_node — not `latest.extra`, and with no recency window at all.
    See identity.py for why both of those are deliberate.

    Having no window is exactly why this cannot ride the scan above: unbounded,
    it would read the whole retained table. So the key test happens in SQL —
    `extra ->> 'prev_node' IS NOT NULL`, which JSON on SQLite and JSONB on
    Postgres both answer — and only the handful of rows a re-identification
    burst leaves behind come back. That predicate is deliberately looser than
    prev_node_from_extra (which wants a non-empty string): a superset is safe,
    the strict rule still runs here in Python.

    Postgres serves it from ix_telemetry_prev_node, a partial index over the
    same predicate — so the scan is proportional to the handful of
    re-identification beats, not to the retained table."""
    reported: dict[int, str] = {}
    if not ids:
        return reported
    rows = await session.execute(
        select(Telemetry.camera_id, Telemetry.extra)
        .where(
            Telemetry.camera_id.in_(ids),
            PREV_NODE_PRESENT,
        )
        .order_by(Telemetry.camera_id, *NEWEST_FIRST)
    )
    for cam_id, extra in rows.all():
        prev = prev_node_from_extra(extra)
        if prev is not None:
            reported.setdefault(cam_id, prev)
    return reported


@nodes_router.get("/nodes/health", response_model=list[NodeHealth])
async def nodes_health(session: AsyncSession = Depends(get_session)):
    q = select(Camera).options(joinedload(Camera.site)).order_by(Camera.name)
    nodes = (await session.scalars(q)).all()
    ids = [n.id for n in nodes]

    latest = await _latest_telemetry(session, ids)
    latest_health, latest_storage, min_push_fails = await _windowed_extras(
        session, ids, utcnow() - HEALTH_WINDOW
    )
    reported_prev = await _reported_prev_nodes(session, ids)

    # Only the ones still split: a reported slug that now resolves to the very
    # row reporting it means the merge already happened, in either direction.
    prev_nodes = await unmerged_prev_nodes(session, nodes, reported_prev)

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
            storage_state=latest_storage.get(n.id),
            prev_node=prev_nodes.get(n.id, (None, None))[0],
            prev_node_id=prev_nodes.get(n.id, (None, None))[1],
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
