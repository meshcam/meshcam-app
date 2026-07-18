"""Gateway-facing command queue (pull model — nodes sleep, the gateway sits
behind the tunnel). Two transports:

- GET /commands — classic poll: returns everything outstanding.
- GET /commands/stream — SSE: emits every outstanding command on connect, then
  pushes new ones the moment they're queued (25 s keepalives; the timeout scan
  doubles as a safety net for missed wake signals).

Re-delivery until done is intentional (the leaf's response is idempotent via
event_id), so the gateway can stay stateless. fetch_full commands complete
automatically when the matching kind=full ingest arrives."""

import asyncio
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from trailcam.auth import current_device
from trailcam.config import get_settings
from trailcam.db import get_session, get_sessionmaker
from trailcam.events import COMMANDS, bus, publish_request_update
from trailcam.models import OUTSTANDING, Camera, Command, Photo, utcnow
from trailcam.schemas import CommandAck, CommandOut

router = APIRouter(prefix="/api/v1", tags=["commands"], dependencies=[Depends(current_device)])

SSE_KEEPALIVE_S = 25


def _command_out(c: Command) -> CommandOut:
    return CommandOut(
        id=c.id,
        kind=c.kind,
        site=c.camera.site.slug,
        node=c.camera.slug,
        event_id=c.event_id,
        payload=c.payload,
        status=c.status,
        created_at=c.created_at,
    )


async def _publish_delivered(session: AsyncSession, delivered: list[Command]) -> None:
    """Push 'request' diagnostics updates for commands that just got delivered."""
    event_ids = [c.event_id for c in delivered if c.event_id]
    if not event_ids:
        return
    rows = await session.execute(
        select(Photo.event_id, Photo.id).where(Photo.event_id.in_(event_ids))
    )
    photo_by_event = dict(rows.all())
    for c in delivered:
        pid = photo_by_event.get(c.event_id)
        if pid is not None:
            publish_request_update(c, pid, c.camera.last_seen_at)


async def _fetch_outstanding(session: AsyncSession, only_pending: bool = False) -> list[Command]:
    # Expire at fetch time, same policy as the nightly purge (command_ttl_days) —
    # this just enforces it promptly instead of waiting for the CronJob. The leaf
    # drops a fetch_full whose event isn't in its store on the promise that it
    # "expires server-side"; without a working TTL those commands re-deliver on
    # every announce forever (observed 2026-07-03: four July-2 commands for
    # wiped-store events still circulating a day later).
    stale = await session.scalars(
        select(Command).where(
            Command.status.in_(OUTSTANDING),
            Command.created_at < utcnow() - timedelta(days=get_settings().command_ttl_days),
        )
    )
    expired = list(stale.all())
    if expired:
        now = utcnow()
        for c in expired:
            c.status, c.completed_at = "expired", now
        await session.commit()

    # Redeliver pending/delivered only: "received" means the node confirmed receipt
    # (bug 8), so re-sending it would just burn the node's announce windows. It stays
    # in OUTSTANDING above for TTL expiry and ingest auto-completion.
    statuses = ("pending",) if only_pending else ("pending", "delivered")
    q = (
        select(Command)
        .options(joinedload(Command.camera).joinedload(Camera.site))
        .where(Command.status.in_(statuses))
        .order_by(Command.id)
    )
    return list((await session.scalars(q)).all())


async def _mark_delivered(session: AsyncSession, rows: list[Command]) -> list[Command]:
    now = utcnow()
    newly = [c for c in rows if c.status == "pending"]
    for c in newly:
        c.status, c.delivered_at = "delivered", now
    if newly:
        await session.commit()
        await _publish_delivered(session, newly)
    return newly


@router.get("/commands", response_model=list[CommandOut])
async def poll_commands(session: AsyncSession = Depends(get_session)):
    rows = await _fetch_outstanding(session)
    await _mark_delivered(session, rows)
    return [_command_out(c) for c in rows]


@router.get("/commands/stream")
async def command_stream():
    """SSE: outstanding commands on connect (a reconnecting gateway re-receives
    undone work), then instant push on newly queued ones."""

    async def deliver(only_pending: bool) -> list[str]:
        async with get_sessionmaker()() as session:
            rows = await _fetch_outstanding(session, only_pending=only_pending)
            await _mark_delivered(session, rows)
            return [
                f"event: command\ndata: {json.dumps(_command_out(c).model_dump(mode='json'))}\n\n"
                for c in rows
            ]

    async def stream():
        async with bus.subscribe(COMMANDS) as queue:
            for frame in await deliver(only_pending=False):
                yield frame
            while True:
                try:
                    await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)
                except TimeoutError:
                    # safety net for missed wake signals + proxy keepalive
                    for frame in await deliver(only_pending=True):
                        yield frame
                    yield ": keepalive\n\n"
                    continue
                for frame in await deliver(only_pending=True):
                    yield frame

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/commands/{command_id}/ack", response_model=CommandOut)
async def ack_command(
    command_id: int, body: CommandAck, session: AsyncSession = Depends(get_session)
):
    cmd = await session.scalar(
        select(Command)
        .options(joinedload(Command.camera).joinedload(Camera.site))
        .where(Command.id == command_id)
    )
    if cmd is None:
        raise HTTPException(404, "command not found")
    if body.status == "received":
        # Leaf receipt ack, relayed by the gateway from the node's announce (bug 8).
        # Never regress a command that already finished — the ack can arrive after
        # the kind=full ingest auto-completed it (announce rides behind the upload).
        if cmd.status in ("pending", "delivered"):
            cmd.status = "received"
            cmd.received_at = utcnow()
            if body.detail:
                cmd.detail = body.detail
            await session.commit()
    else:
        cmd.status = body.status
        cmd.detail = body.detail
        cmd.completed_at = utcnow()
        await session.commit()
    if cmd.event_id:
        photo_id = await session.scalar(select(Photo.id).where(Photo.event_id == cmd.event_id))
        if photo_id is not None:
            publish_request_update(cmd, photo_id, cmd.camera.last_seen_at)
    return _command_out(cmd)
