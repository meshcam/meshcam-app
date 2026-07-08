"""In-process pub/sub for live UI updates (SSE).

Single-replica app, so every state transition (request queued, gateway pickup,
full-res ingest) happens in this process — a broadcast bus covers it without
Redis/postgres LISTEN. Topics are strings (e.g. "photo:<event_id>"); payloads
are small dicts that just say *something changed* — subscribers re-query the DB
for the authoritative snapshot, so missed/coalesced events are harmless."""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_QUEUE_SIZE = 16


class EventBus:
    def __init__(self) -> None:
        self._topics: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)

    def publish(self, topic: str, payload: dict) -> None:
        for queue in self._topics.get(topic, ()):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # subscriber is behind; it re-queries on the next event anyway

    @asynccontextmanager
    async def subscribe(self, topic: str) -> AsyncIterator[asyncio.Queue[dict]]:
        queue: asyncio.Queue[dict] = asyncio.Queue(_QUEUE_SIZE)
        self._topics[topic].add(queue)
        try:
            yield queue
        finally:
            self._topics[topic].discard(queue)
            if not self._topics[topic]:
                del self._topics[topic]


bus = EventBus()

# One multiplexed browser stream + one device stream. Payloads on FEED are
# {"event": <sse event name>, "data": {...fully resolved by the publisher...}};
# COMMANDS messages are bare wake signals (subscribers re-query outstanding).
FEED = "feed"
COMMANDS = "commands"


def publish_request_update(cmd, photo_id, node_last_seen_at) -> None:
    """FEED 'request' event — the overlay's diagnostics snapshot for one photo."""
    bus.publish(
        FEED,
        {
            "event": "request",
            "data": {
                "photo_id": str(photo_id),
                "request": {
                    "status": cmd.status,
                    "quality": (cmd.payload or {}).get("quality", "standard"),
                    "requested_by": cmd.requested_by,
                    "created_at": cmd.created_at.isoformat(),
                    "delivered_at": cmd.delivered_at.isoformat() if cmd.delivered_at else None,
                    "completed_at": cmd.completed_at.isoformat() if cmd.completed_at else None,
                    "detail": cmd.detail,
                    "node_last_seen_at": (
                        node_last_seen_at.isoformat() if node_last_seen_at else None
                    ),
                },
            },
        },
    )
