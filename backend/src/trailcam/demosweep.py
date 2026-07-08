"""Demo housekeeping: `python -m trailcam.demosweep` (CronJob, demo overlay only).

The demo's full-res quality is a *loaner*: a visitor requests it, the
simulated mesh delivers it, and about an hour later this sweep takes it back
so the next visitor gets the thumbnail-first experience (and can run the
request themselves). Specifically:

- photos whose full-res landed more than DEMOSWEEP_RETAIN_MINUTES ago revert
  to thumbnail-only (S3 full object deleted, row fields nulled) and their
  fetch_full command history is cleared so a fresh request is possible;
- outstanding commands that have sat undelivered/unanswered for over an hour
  are failed (a dead simulator must not brick the request buttons);
- finished command history (done/failed/expired) older than the retain window
  is dropped;
- telemetry older than DEMOSWEEP_TELEMETRY_DAYS is pruned so the live
  simulator's beats don't grow the table forever.

The seeded thumbnails and originals/ (the simulator's "SD card") are never
touched. Refuses to run unless TRAILCAM_DEMO_MODE is on.
"""

import asyncio
import logging
import os
import sys
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.config import get_settings
from trailcam.db import get_engine
from trailcam.models import OUTSTANDING, Command, Photo, Telemetry, utcnow
from trailcam.s3 import get_store, raw_variant

logger = logging.getLogger("demosweep")

RETAIN_MINUTES = int(os.environ.get("DEMOSWEEP_RETAIN_MINUTES", "60"))
STUCK_MINUTES = int(os.environ.get("DEMOSWEEP_STUCK_MINUTES", "60"))
TELEMETRY_DAYS = int(os.environ.get("DEMOSWEEP_TELEMETRY_DAYS", "7"))


async def sweep() -> None:
    now = utcnow()
    engine = get_engine()
    store = get_store()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        # 1) take back full-res that has been enjoyed long enough
        cutoff = now - timedelta(minutes=RETAIN_MINUTES)
        done_old = (
            select(Command.event_id)
            .where(
                Command.kind == "fetch_full",
                Command.status == "done",
                Command.completed_at < cutoff,
            )
            .scalar_subquery()
        )
        photos = (
            await session.scalars(
                select(Photo).where(Photo.full_key.is_not(None), Photo.event_id.in_(done_old))
            )
        ).all()
        if photos:
            full_keys = [p.full_key for p in photos if p.full_key]
            await store.delete(full_keys + [raw_variant(k) for k in full_keys])
            event_ids = [p.event_id for p in photos]
            for p in photos:
                p.full_key, p.full_size = None, None
            # clear the request history so the next visitor can request again
            await session.execute(
                delete(Command).where(
                    Command.kind == "fetch_full", Command.event_id.in_(event_ids)
                )
            )
            logger.info("reset %d photo(s) to thumbnail-only: %s", len(photos), event_ids)

        # 2) fail requests the simulator never answered (crashed / redeploying)
        stuck = (
            await session.scalars(
                select(Command).where(
                    Command.kind == "fetch_full",
                    Command.status.in_(OUTSTANDING),
                    Command.created_at < now - timedelta(minutes=STUCK_MINUTES),
                )
            )
        ).all()
        for c in stuck:
            c.status = "failed"
            c.detail = "demo sweep: transfer never completed — request again"
            c.completed_at = now
        if stuck:
            logger.info("failed %d stuck command(s)", len(stuck))

        # 3) drop finished command history past the retain window
        await session.execute(
            delete(Command).where(
                Command.status.in_(("done", "failed", "expired")),
                Command.completed_at < cutoff,
            )
        )

        # 4) keep telemetry to the backfill horizon
        pruned = await session.execute(
            delete(Telemetry).where(
                Telemetry.received_at < now - timedelta(days=TELEMETRY_DAYS)
            )
        )
        if pruned.rowcount:
            logger.info("pruned %d old telemetry row(s)", pruned.rowcount)

        await session.commit()
    await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not get_settings().demo_mode:
        sys.exit("demosweep refuses to run without TRAILCAM_DEMO_MODE=1")
    asyncio.run(sweep())


if __name__ == "__main__":
    main()
