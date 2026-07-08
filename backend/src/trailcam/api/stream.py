"""Multiplexed browser SSE — ONE connection per tab carries every live update,
routed client-side by SSE event name:

  photo         — new/updated photo (full PhotoOut + change: new|updated)
  photo_removed — {id}
  request       — fetch_full diagnostics snapshot ({photo_id, request})
  node          — {node_id} (telemetry heartbeat landed; client refetches)

Publishers resolve payloads fully, so this endpoint is just a pump. The bus is
in-process (single replica); if the app ever scales out, swap events.py for
LISTEN/NOTIFY without touching this."""

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from trailcam.auth import current_user
from trailcam.events import FEED, bus

router = APIRouter(prefix="/api/v1", tags=["stream"], dependencies=[Depends(current_user)])

SSE_KEEPALIVE_S = 25


@router.get("/events")
async def user_events():
    async def stream():
        async with bus.subscribe(FEED) as queue:
            yield ": connected\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
