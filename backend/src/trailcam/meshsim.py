"""The demo's simulated mesh: `python -m trailcam.meshsim`.

Plays the part of the Hemlock Hollow hardware — one gateway, two relays, ten
cameras (trailcam/demomesh.py) — as a *client* of the real device API, so a
demo visitor's full-res request goes through the exact production pipeline:

    request-full → Command queued → THIS process polls /commands like a real
    gateway → waits for the leaf's next listen window → radio-profile grant →
    16 KB chunks at measured LoRa goodput (one at a time; it's one radio
    channel) → POST /ingest kind=full → the photo upgrades over SSE.

Field-realistic timing is the point: standard ≈ 2-4 minutes, max (the QXGA
sensor original) ≈ 10-15 minutes, with the occasional resource failure and
retry. Every step is beaconed as telemetry so the mesh-traffic panel narrates
the whole pull while the visitor watches.

The full frames come from originals/{event_id}.{quality}.jpg in the demo
bucket (seeded; think of it as the leaf's SD card). Auth is a normal device
bearer token (MESHSIM_DEVICE_TOKEN).

Env:
    TRAILCAM_API_URL        base URL of the app (default http://127.0.0.1:8000)
    MESHSIM_DEVICE_TOKEN    device bearer token (required)
    MESHSIM_SPEED           time divisor for local testing (default 1.0;
                            10 = everything 10x faster)
    TRAILCAM_S3_*           bucket with the originals (same secret as the app)
"""

import asyncio
import logging
import os
import random
import sys
from datetime import UTC, datetime

import httpx

from trailcam import demomesh
from trailcam.s3 import get_store

logger = logging.getLogger("meshsim")

API = os.environ.get("TRAILCAM_API_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("MESHSIM_DEVICE_TOKEN", "")
SPEED = float(os.environ.get("MESHSIM_SPEED", "1.0"))

COMMAND_POLL_S = 30
RETRY_MAX = 2

rng = random.Random()  # live behavior should differ run to run


def now() -> datetime:
    return datetime.now(UTC)


async def snooze(seconds: float) -> None:
    await asyncio.sleep(max(0.05, seconds / SPEED))


class Gateway:
    """The whole simulated mesh in one client: telemetry heartbeats per node,
    a command poller, and a single-radio transfer queue."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            base_url=API,
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30.0,
        )
        self.store = get_store()
        self.transfers: asyncio.Queue[dict] = asyncio.Queue()
        self.queued_ids: set[int] = set()
        # gateway heartbeat counters, roughly continuing where the seed left off
        self.counters = {
            "announces": 900 + rng.randint(0, 90),
            "uploads": 21,
            "chunks": 0,
            "res_ok": 0,
            "res_fail": 0,
        }

    # --- plumbing -------------------------------------------------------------

    async def post_beat(self, node: demomesh.Node, extra: dict) -> None:
        body = demomesh.telemetry_body(node, now(), rng, extra)
        try:
            r = await self.http.post("/api/v1/telemetry", json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("telemetry beat for %s failed: %s", node.slug, exc)

    # --- long-running loops ----------------------------------------------------

    async def node_loop(self, node: demomesh.Node) -> None:
        """Periodic check-ins (cameras/relays) or heartbeats (gateway)."""
        interval_h = demomesh.CHECKIN_INTERVAL_H[node.kind]
        # stagger starts so a restart doesn't announce all 13 nodes at once
        await snooze(rng.uniform(5, interval_h * 3600 * 0.5))
        while True:
            if node.kind == "gateway":
                hour = now().hour + now().minute / 60
                self.counters["announces"] += 1
                extra = demomesh.gateway_extra(dict(self.counters), hour, rng)
            else:
                extra = demomesh.leaf_beat_extra(node.slug, now(), "checkin", rng)
                self.counters["announces"] += 1
            await self.post_beat(node, extra)
            await snooze(interval_h * 3600 * rng.uniform(0.92, 1.08))

    async def false_trigger_loop(self) -> None:
        """A couple of times a day some camera's PIR fires on nothing."""
        while True:
            await snooze(rng.uniform(6, 14) * 3600)
            node = rng.choice(demomesh.CAMERAS)
            extra = demomesh.leaf_beat_extra(
                node.slug, now(), f"alert:{rng.randint(2100, 4400)}", rng
            )
            await self.post_beat(node, extra)

    async def command_loop(self) -> None:
        """Poll like the real gateway; queue fetch_full work exactly once."""
        while True:
            try:
                r = await self.http.get("/api/v1/commands")
                r.raise_for_status()
                for cmd in r.json():
                    if cmd["id"] in self.queued_ids:
                        continue
                    self.queued_ids.add(cmd["id"])
                    if cmd["kind"] == "fetch_full":
                        self.transfers.put_nowait(cmd)
                        logger.info(
                            "queued fetch_full #%s %s (%s)",
                            cmd["id"],
                            cmd["event_id"],
                            (cmd.get("payload") or {}).get("quality", "standard"),
                        )
                    else:
                        # nothing else is meaningful on a simulated mesh
                        await self.ack(cmd["id"], "done", "meshsim: no-op")
            except httpx.HTTPError as exc:
                logger.warning("command poll failed: %s", exc)
            await snooze(COMMAND_POLL_S)

    async def ack(self, command_id: int, status: str, detail: str | None = None) -> None:
        try:
            r = await self.http.post(
                f"/api/v1/commands/{command_id}/ack",
                json={"status": status, "detail": detail},
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("ack %s -> %s failed: %s", command_id, status, exc)

    # --- the transfer worker (one radio channel, one pull at a time) -----------

    async def transfer_worker(self) -> None:
        while True:
            cmd = await self.transfers.get()
            try:
                await self.run_transfer(cmd)
            except Exception:
                logger.exception("transfer for %s blew up", cmd.get("event_id"))
                await self.ack(cmd["id"], "failed", "meshsim: internal error")

    async def run_transfer(self, cmd: dict) -> None:
        event_id = cmd["event_id"]
        quality = (cmd.get("payload") or {}).get("quality", "standard")
        node = demomesh.BY_SLUG.get(cmd["node"])
        if node is None:
            await self.ack(cmd["id"], "failed", f"meshsim: unknown node {cmd['node']}")
            return

        try:
            body = await self.read_original(event_id, quality)
        except Exception:
            logger.warning("no original for %s (%s)", event_id, quality)
            await self.ack(cmd["id"], "failed", "meshsim: original missing from SD")
            return

        # the leaf is asleep; the command goes out on its next listen window
        await snooze(rng.uniform(*demomesh.WAKE_DELAY_S))

        # ADR steps the link up for bulk transfer
        grant, confirmed = demomesh.rf_grant_extra(rng)
        await self.post_beat(node, grant)
        await snooze(rng.uniform(4, 11))
        await self.post_beat(node, confirmed)
        await snooze(rng.uniform(2, 6))

        plan = demomesh.chunk_plan(len(body), rng)
        chunks = len(plan)
        wall_start = now()
        sent = 0
        for chunk_no, nbytes, seconds in plan:
            for attempt in range(RETRY_MAX + 1):
                await snooze(seconds)
                if attempt < RETRY_MAX and rng.random() < demomesh.CHUNK_ERROR_PROB:
                    self.counters["res_fail"] += 1
                    await self.post_beat(node, {"transfer_error": "resource_failed_7"})
                    await snooze(rng.uniform(8, 20))
                    continue
                self.counters["chunks"] += 1
                await self.post_beat(
                    node,
                    demomesh.chunk_extra(
                        event_id, quality, chunk_no, chunks, nbytes, len(body), seconds
                    ),
                )
                sent += nbytes
                break
            else:
                await self.ack(cmd["id"], "failed", "resource_failed_7 — giving up this pass")
                return
            await snooze(rng.uniform(*demomesh.CHUNK_GAP_S))

        wall_s = max(0.5, (now() - wall_start).total_seconds() * SPEED)
        await self.post_beat(
            node, demomesh.reassembled_extra(event_id, quality, chunks, len(body), wall_s)
        )
        self.counters["res_ok"] += 1
        self.counters["uploads"] += 1

        # hand the reassembled file to ingest — completes the command server-side
        captured_at = self.captured_at_from_event(event_id)
        files = {"file": (f"{event_id}.full.jpg", body, "image/jpeg")}
        data = {
            "site": cmd["site"],
            "camera": cmd["node"],
            "event_id": event_id,
            "captured_at": captured_at.isoformat(),
            "kind": "full",
        }
        r = await self.http.post("/api/v1/ingest", data=data, files=files)
        r.raise_for_status()
        logger.info(
            "delivered %s %s (%.0f KB in %.0f s)", event_id, quality, len(body) / 1024, wall_s
        )

    async def read_original(self, event_id: str, quality: str) -> bytes:
        key = f"originals/{event_id}.{quality}.jpg"
        parts = [chunk async for chunk in self.store.stream(key)]
        return b"".join(parts)

    @staticmethod
    def captured_at_from_event(event_id: str) -> datetime:
        """Leaf event ids are `<leaf>-<epoch>-<seq>`; recover the capture time
        the same way the real gateway does."""
        try:
            return datetime.fromtimestamp(int(event_id.split("-")[-2]), UTC)
        except (ValueError, IndexError):
            return now()

    async def run(self) -> None:
        logger.info(
            "meshsim up: %d nodes vs %s (speed %.0fx)", len(demomesh.NODES), API, SPEED
        )
        async with asyncio.TaskGroup() as tg:
            for node in demomesh.NODES:
                tg.create_task(self.node_loop(node))
            tg.create_task(self.false_trigger_loop())
            tg.create_task(self.command_loop())
            tg.create_task(self.transfer_worker())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not TOKEN:
        sys.exit("MESHSIM_DEVICE_TOKEN is required")
    asyncio.run(Gateway().run())


if __name__ == "__main__":
    main()
