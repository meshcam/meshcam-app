"""Retention sweep: delete expired, un-kept photos (S3 objects + rows).

Run as `python -m trailcam.purge` — a nightly k8s CronJob per environment.
`keep=true` photos have expires_at NULL, so they can never match."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select, update

from trailcam.config import get_settings
from trailcam.db import get_sessionmaker
from trailcam.models import OUTSTANDING, Command, Photo, Telemetry, utcnow
from trailcam.s3 import get_store, raw_variant

log = logging.getLogger("trailcam.purge")

BATCH = 200


async def purge_once() -> int:
    total = 0
    async with get_sessionmaker()() as session:
        while True:
            photos = (
                await session.scalars(
                    select(Photo)
                    .where(Photo.keep.is_(False), Photo.expires_at < utcnow())
                    .limit(BATCH)
                )
            ).all()
            if not photos:
                break
            keys = [k for p in photos for k in (p.thumb_key, p.full_key) if k]
            keys += [raw_variant(p.full_key) for p in photos if p.full_key]
            await get_store().delete(keys)
            for p in photos:
                await session.delete(p)
            await session.commit()
            total += len(photos)
    return total


async def expire_commands() -> int:
    """Outstanding commands older than command_ttl_days give up (node likely
    unreachable / photo no longer worth fetching)."""
    cutoff = utcnow() - timedelta(days=get_settings().command_ttl_days)
    async with get_sessionmaker()() as session:
        result = await session.execute(
            update(Command)
            .where(Command.status.in_(OUTSTANDING), Command.created_at < cutoff)
            .values(status="expired", completed_at=utcnow())
        )
        await session.commit()
        return result.rowcount or 0


async def purge_telemetry() -> int:
    cutoff = utcnow() - timedelta(days=get_settings().telemetry_ttl_days)
    async with get_sessionmaker()() as session:
        result = await session.execute(delete(Telemetry).where(Telemetry.received_at < cutoff))
        await session.commit()
        return result.rowcount or 0


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    n = await purge_once()
    log.info("purged %d expired photos", n)
    t = await purge_telemetry()
    log.info("purged %d aged telemetry rows", t)
    c = await expire_commands()
    log.info("expired %d stale commands", c)


if __name__ == "__main__":
    asyncio.run(main())
