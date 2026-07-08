"""Seed the demo stack: Hemlock Hollow, an imagined ~100-acre NE-Ohio property.

What this writes (see trailcam/demomesh.py for the property + mesh model):

- One site, ten cameras, two relays, a gateway — the roster the live
  simulator (trailcam.meshsim) animates after seeding.
- Photos as **thumbnails only**: the detector's bbox crop, few KB, exactly
  what a leaf pushes over LoRa unprompted. Full-res quality is *requested*
  by demo visitors and delivered by the simulator through the real
  ingest pipeline at field-realistic speed.
- The full-frame JPEGs the simulator serves ("the SD card"):
  originals/{event_id}.standard.jpg / .max.jpg in the same bucket, never
  exposed by the app.
- 7 days of telemetry backfill (check-ins with announce packets, motion
  alerts, gateway heartbeats) so the charts and mesh feed have history.
- The simulator's device token (MESHSIM_DEVICE_TOKEN env; generated and
  printed if unset).

Fixtures are CC wildlife photos (fixtures/manifest.json for attribution),
processed by trailcamify.py to match the leaf hardware: QXGA sensor frame,
IR treatment at night. Originals are stored CLEAN — the ingest pipeline
burns the OSD bar in when the simulator delivers them (trailcam.osd).
Nothing here describes a real property — names, layout, and identities
are invented.

Run from the repo root via run.sh, or by hand with the demo env (see
homelab kubernetes/apps/trailcam/README.md).
"""

import asyncio
import os
import random
import sys
import zlib
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))

import trailcamify  # noqa: E402 — sibling module

from trailcam import demomesh, osd  # noqa: E402
from trailcam.auth import hash_token, mint_token  # noqa: E402
from trailcam.config import get_settings  # noqa: E402
from trailcam.db import get_engine  # noqa: E402
from trailcam.models import Camera, DeviceToken, Photo, Site, Telemetry, utcnow  # noqa: E402
from trailcam.s3 import get_store  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
EASTERN = ZoneInfo("America/New_York")
rng = random.Random(2026)  # deterministic seed data → reproducible screenshots

# fixture -> (detector bbox l,t,r,b as frame fractions, force IR look, pre-crop)
# bboxes were eyeballed per fixture; generous padding like a real detector crop.
# force_ir renders daylight/snow shots as night IR — hides out-of-season snow
# and matches the capture times below. pre-crop (l,t,r,b source fractions, or
# None) trims another vendor's burned-in OSD bar off genuine trailcam sources
# before our pipeline stamps its own.
FIXTURE_LOOK = {
    "black-bear.jpg": ((0.52, 0.25, 0.92, 0.80), False, None),
    "turkey-tom.jpg": ((0.10, 0.05, 1.00, 1.00), False, None),
    "turkeys-trail.jpg": ((0.15, 0.32, 0.70, 0.82), False, None),
    "ir-raccoons.jpg": ((0.35, 0.10, 1.00, 0.80), False, None),
    "raccoon-fence.jpg": ((0.24, 0.20, 0.85, 0.85), True, None),
    "buck-frost-far.jpg": ((0.48, 0.30, 0.82, 0.82), True, None),
    "buck-frost-near.jpg": ((0.36, 0.20, 0.92, 0.88), True, None),
    "fawns-creek.jpg": ((0.15, 0.28, 0.80, 0.90), False, None),
    "coyote-pounce.jpg": ((0.22, 0.18, 0.82, 0.80), True, None),
    "ir-deer-pond.jpg": ((0.30, 0.00, 0.65, 0.65), False, None),
    "red-fox.jpg": ((0.30, 0.00, 0.90, 1.00), False, None),
    "ir-bucks-sparring.jpg": ((0.00, 0.00, 1.00, 1.00), False, None),
    "fawn-grazing.jpg": ((0.00, 0.00, 0.80, 0.95), False, None),
    "ir-deer-woods.jpg": ((0.22, 0.00, 0.80, 0.75), False, None),
    "buck-velvet-pines.jpg": ((0.30, 0.25, 0.85, 0.95), False, None),
    "buck-velvet-grass.jpg": ((0.05, 0.00, 0.95, 1.00), False, None),
    # 2026-07: genuine camera-trap sources (pmuellr / USFWS refuge cams /
    # Virginia State Parks / jano71) — see manifest.json for attribution.
    "ir-raccoon-forage.jpg": ((0.50, 0.35, 0.85, 0.75), False, (0, 0, 1, 0.935)),
    "ir-doe-stare.jpg": ((0.28, 0.00, 0.75, 0.85), False, (0, 0, 1, 0.935)),
    "ir-rabbits.jpg": ((0.32, 0.45, 0.72, 0.88), False, (0, 0, 1, 0.935)),
    "ir-possum-tail.jpg": ((0.00, 0.50, 0.45, 1.00), False, (0, 0, 1, 0.935)),
    "ir-raccoon-stand.jpg": ((0.50, 0.30, 0.95, 0.90), False, None),
    "raccoon-pair.jpg": ((0.20, 0.20, 0.75, 1.00), False, None),
    "coyote-fall.jpg": ((0.10, 0.05, 0.90, 0.95), False, None),
    "ir-skunk-tail.jpg": ((0.28, 0.40, 0.75, 1.00), False, None),
    "skunk-day.jpg": ((0.28, 0.38, 0.75, 0.80), False, (0, 0, 1, 0.93)),
    "turkey-flock.jpg": ((0.00, 0.25, 1.00, 0.95), False, (0, 0, 1, 0.93)),
    "bobcat-pair.jpg": ((0.18, 0.22, 0.85, 1.00), False, None),
    "squirrel-dash.jpg": ((0.30, 0.72, 0.88, 1.00), False, None),
    "ir-buck-walk.jpg": ((0.35, 0.28, 0.85, 0.80), False, (0, 0, 1, 0.94)),
    "ir-coyote-night.jpg": ((0.62, 0.28, 1.00, 0.60), False, (0, 0.045, 1, 0.955)),
    "ir-fox-orchard.jpg": ((0.20, 0.68, 0.70, 1.00), False, (0, 0, 1, 0.92)),
}

# fixture -> (camera slug, days ago, local capture time, keep, burst frames)
# Times are Eastern and match the look (IR shots at night). burst > 1 seeds
# extra detector triggers seconds apart with a shifted crop — the second and
# third frames of a real PIR burst.
PHOTOS = [
    ("black-bear.jpg", "swamp-edge", 6, "09:24", True, 1),
    ("ir-doe-stare.jpg", "beech-ridge", 6, "21:47", False, 1),
    ("turkey-tom.jpg", "hayfield-gate", 6, "17:52", False, 1),
    ("ir-raccoons.jpg", "creek-crossing", 5, "23:41", False, 1),
    ("turkey-flock.jpg", "hayfield-gate", 5, "16:41", False, 2),
    ("squirrel-dash.jpg", "pine-thicket", 5, "11:26", False, 1),
    ("raccoon-fence.jpg", "old-orchard", 5, "02:37", False, 1),
    ("ir-raccoon-forage.jpg", "creek-crossing", 5, "01:12", False, 1),
    ("buck-frost-far.jpg", "oak-flats", 4, "05:12", False, 1),
    ("buck-frost-near.jpg", "oak-flats", 4, "05:18", True, 1),
    ("fawns-creek.jpg", "creek-crossing", 4, "10:03", False, 2),
    ("raccoon-pair.jpg", "creek-crossing", 4, "16:19", False, 1),
    ("ir-skunk-tail.jpg", "lake-path", 4, "23:55", False, 1),
    ("ir-fox-orchard.jpg", "old-orchard", 4, "03:19", False, 1),
    ("coyote-pounce.jpg", "hayfield-gate", 3, "22:29", False, 1),
    ("ir-deer-pond.jpg", "beaver-pond", 3, "03:44", False, 2),
    ("bobcat-pair.jpg", "swamp-edge", 3, "19:58", True, 1),
    ("ir-buck-walk.jpg", "oak-flats", 3, "23:16", False, 1),
    ("red-fox.jpg", "lake-path", 2, "08:16", False, 1),
    ("turkeys-trail.jpg", "lake-path", 2, "15:35", False, 2),
    ("skunk-day.jpg", "swamp-edge", 2, "18:59", False, 1),
    ("ir-rabbits.jpg", "hayfield-gate", 2, "22:22", False, 2),
    ("ir-bucks-sparring.jpg", "food-plot", 1, "23:02", True, 1),
    ("fawn-grazing.jpg", "old-orchard", 1, "09:47", False, 1),
    ("ir-deer-woods.jpg", "beech-ridge", 1, "20:58", False, 1),
    ("coyote-fall.jpg", "oak-flats", 1, "15:52", False, 1),
    ("ir-possum-tail.jpg", "old-orchard", 1, "21:31", False, 1),
    ("ir-raccoon-stand.jpg", "beaver-pond", 1, "02:44", False, 1),
    ("ir-coyote-night.jpg", "beech-ridge", 0, "01:44", False, 1),
    ("buck-velvet-pines.jpg", "pine-thicket", 0, "07:21", False, 1),
    ("buck-velvet-grass.jpg", "food-plot", 0, "06:55", False, 2),
]

# false PIR triggers (wind, sun flicker) — motion alert beats with no photo
FALSE_TRIGGERS = 7


def jitter_bbox(bbox: tuple[float, float, float, float], r: random.Random):
    """A burst frame's detector box: same subject, slightly shifted/regrown."""
    left, top, right, bottom = bbox
    dx = r.uniform(-0.04, 0.04)
    dy = r.uniform(-0.03, 0.03)
    return (
        min(max(left + dx, 0.0), 0.9),
        min(max(top + dy, 0.0), 0.9),
        max(min(right + dx, 1.0), 0.1),
        max(min(bottom + dy, 1.0), 0.1),
    )


def capture_time(now: datetime, days_ago: int, local_hhmm: str) -> datetime:
    hh, mm = (int(p) for p in local_hhmm.split(":"))
    local_now = now.astimezone(EASTERN)
    captured = datetime.combine(
        local_now.date() - timedelta(days=days_ago),
        time(hh, mm, rng.randint(0, 59)),
        tzinfo=EASTERN,
    )
    if captured > local_now - timedelta(minutes=30):
        captured -= timedelta(days=1)
    return captured.astimezone(now.tzinfo)


async def ensure_bucket() -> None:
    s = get_settings()
    store = get_store()
    async with store._session.client(**store._client_kwargs) as c:  # noqa: SLF001
        try:
            await c.create_bucket(Bucket=s.s3_bucket)
        except Exception:  # noqa: BLE001 — already exists
            pass


async def main() -> None:
    await ensure_bucket()
    store = get_store()
    now = utcnow()

    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        site = Site(slug=demomesh.SITE_SLUG, name=demomesh.SITE_NAME)
        session.add(site)
        await session.flush()

        nodes: dict[str, Camera] = {}
        for n in demomesh.NODES:
            nodes[n.slug] = Camera(site_id=site.id, slug=n.slug, name=n.name, kind=n.kind)
        session.add_all(nodes.values())
        await session.flush()

        # The simulator's gateway token (sha256 in DB, plaintext via env/stdout).
        sim_token = os.environ.get("MESHSIM_DEVICE_TOKEN") or mint_token()
        session.add(DeviceToken(name="meshsim-gateway", token_hash=hash_token(sim_token)))
        if not os.environ.get("MESHSIM_DEVICE_TOKEN"):
            print(f"generated MESHSIM_DEVICE_TOKEN (save it):\n{sim_token}")

        # --- photos: detector-crop thumbnails only ---------------------------
        # camera slug -> [(at, extra)] mesh beats to backfill alongside heartbeats
        beats: dict[str, list[tuple[datetime, dict]]] = {n.slug: [] for n in demomesh.NODES}
        photo_times: list[datetime] = []
        seq = {n.slug: rng.randint(2, 9) for n in demomesh.NODES}

        for fname, cam_slug, days_ago, hhmm, keep, burst in PHOTOS:
            node = demomesh.BY_SLUG[cam_slug]
            cam = nodes[cam_slug]
            bbox, force_ir, pre_crop = FIXTURE_LOOK[fname]
            ir = force_ir or fname.startswith("ir-")
            src = Image.open(FIXTURES / fname)
            if pre_crop is not None:  # trim another vendor's burned-in OSD bar
                w, h = src.size
                left, top, right, bottom = pre_crop
                src = src.crop(
                    (round(left * w), round(top * h), round(right * w), round(bottom * h))
                )
            first_at = capture_time(now, days_ago, hhmm)

            for i in range(burst):
                captured = first_at + timedelta(seconds=i * rng.randint(3, 9))
                received = captured + timedelta(seconds=rng.randint(18, 55))
                seq[cam_slug] += 1
                event_id = f"{node.leaf_id}-{int(captured.timestamp())}-{seq[cam_slug]}"
                frame = trailcamify.process(
                    src, ir=ir, seed=zlib.crc32(f"{fname}-{i}".encode())
                )
                battery = demomesh.battery_now(node, captured, rng)
                hour = (captured.hour + captured.minute / 60) % 24
                temp = round(demomesh.diurnal_temp(hour) + rng.uniform(-1, 1), 1)
                rssi, snr = demomesh.node_signal(node, rng)
                meta = {
                    "battery_v": battery,
                    "rssi": rssi,
                    "snr": snr,
                    "temp_c": temp,
                }

                box = bbox if i == 0 else jitter_bbox(bbox, rng)
                # thumbs bypass ingest here (seed backdates received_at), so
                # apply the same OSD stamp ingest would (compact bar at 320px)
                thumb = osd.stamp(
                    trailcamify.thumb_jpeg(frame, box),
                    camera_label=node.name,
                    captured_at=captured,
                    temp_c=temp,
                    battery_v=battery,
                    tz="America/New_York",
                )

                key_base = f"{demomesh.SITE_SLUG}/{cam_slug}/{captured:%Y/%m}/{event_id}"
                await store.put(f"{key_base}.thumb.jpg", thumb, "image/jpeg")
                # the "SD card": CLEAN full frames the simulator delivers on
                # request — the ingest pipeline burns the OSD bar in on
                # delivery (trailcam.osd), pulling temp/battery from this
                # photo's meta, exactly like production.
                for tier in ("standard", "max"):
                    await store.put(
                        f"originals/{event_id}.{tier}.jpg",
                        trailcamify.jpeg_tier(frame, tier),
                        "image/jpeg",
                    )

                session.add(
                    Photo(
                        event_id=event_id,
                        camera_id=cam.id,
                        captured_at=captured,
                        received_at=received,
                        thumb_key=f"{key_base}.thumb.jpg",
                        thumb_size=len(thumb),
                        meta=meta,
                        keep=keep and i == 0,
                        expires_at=None,
                    )
                )
                photo_times.append(received)
                beats[cam_slug].append(
                    (
                        captured + timedelta(seconds=rng.randint(2, 6)),
                        demomesh.leaf_beat_extra(
                            cam_slug, captured, f"alert:{rng.randint(2500, 5700)}", rng
                        ),
                    )
                )
                cam.last_seen_at = max(cam.last_seen_at or received, received)

        # false PIR triggers: alert beats with no photo behind them
        cam_slugs = [n.slug for n in demomesh.CAMERAS]
        for _ in range(FALSE_TRIGGERS):
            slug = rng.choice(cam_slugs)
            at = now - timedelta(hours=rng.uniform(1, 7 * 24))
            beats[slug].append(
                (at, demomesh.leaf_beat_extra(slug, at, f"alert:{rng.randint(2100, 4200)}", rng))
            )

        # --- telemetry backfill: 7 days of heartbeats ------------------------
        photo_times.sort()
        announces_final = sum(
            round(7 * 24 / demomesh.CHECKIN_INTERVAL_H[n.kind])
            for n in demomesh.NODES
            if n.kind != "gateway"
        ) + len(photo_times)

        for n in demomesh.NODES:
            cam = nodes[n.slug]
            interval_h = demomesh.CHECKIN_INTERVAL_H[n.kind]
            t_h = 7 * 24.0
            uptime = rng.randint(3, 21) * 86400
            first = True
            while t_h > 0:
                at = now - timedelta(hours=t_h)
                if n.kind == "gateway":
                    frac = 1 - t_h / (7 * 24)
                    hour = (at.hour + at.minute / 60) % 24
                    extra = demomesh.gateway_extra(
                        {
                            "announces": round(announces_final * frac),
                            "uploads": sum(1 for p in photo_times if p <= at),
                            "chunks": 0,
                            "res_ok": 0,
                            "res_fail": 0,
                        },
                        hour,
                        rng,
                    )
                else:
                    status = "hello" if first else "checkin"
                    extra = demomesh.leaf_beat_extra(n.slug, at, status, rng)
                body = demomesh.telemetry_body(n, at, rng, extra)
                session.add(
                    Telemetry(
                        camera_id=cam.id,
                        received_at=at,
                        reported_at=at - timedelta(seconds=rng.randint(1, 8)),
                        battery_v=body["battery_v"],
                        temp_c=body["temp_c"],
                        pressure_hpa=body["pressure_hpa"],
                        rssi=body.get("rssi"),
                        snr=body.get("snr"),
                        uptime_s=uptime - round(t_h * 3600),
                        boot_reason=body["boot_reason"],
                        fw_version=body["fw_version"],
                        extra=extra,
                    )
                )
                if first and n.kind != "gateway":
                    session.add(
                        Telemetry(
                            camera_id=cam.id,
                            received_at=at + timedelta(seconds=rng.randint(3, 9)),
                            fw_version="0.5.0",
                            extra={"via": "mesh", "link": "up"},
                        )
                    )
                first = False
                cam.last_seen_at = at
                cam.last_battery_v = body["battery_v"]
                t_h -= interval_h + rng.uniform(-0.08, 0.08)

        # --- photo-driven mesh beats (alerts) --------------------------------
        beat_rows = 0
        for slug, rows in beats.items():
            cam = nodes[slug]
            node = demomesh.BY_SLUG[slug]
            for at, extra in rows:
                if at > now:
                    continue
                rssi, snr = demomesh.node_signal(node, rng)
                session.add(
                    Telemetry(
                        camera_id=cam.id,
                        received_at=at,
                        rssi=rssi,
                        snr=snr,
                        fw_version="0.5.0",
                        extra=extra,
                    )
                )
                beat_rows += 1
                if at > (cam.last_seen_at or at):
                    cam.last_seen_at = at

        await session.commit()
    await engine.dispose()
    n_photos = sum(b for *_x, b in PHOTOS)
    print(
        f"seeded {n_photos} thumbnail-only photos, {len(demomesh.NODES)} nodes, "
        f"7d telemetry, {beat_rows} mesh beats, originals for the simulator"
    )


if __name__ == "__main__":
    asyncio.run(main())
