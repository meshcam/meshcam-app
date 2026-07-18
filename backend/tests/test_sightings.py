"""Sighting grouping: burst adoption, gap splits, straggler bridge merges,
and the 0009 backfill's frozen copy of the same walk."""

import importlib.util
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from trailcam.db import get_engine, get_sessionmaker
from trailcam.models import Photo, utcnow

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


async def ingest(client, token, event_id, captured, camera="c3-back-of-lake"):
    r = await client.post(
        "/api/v1/ingest",
        data={
            "site": "north40",
            "camera": camera,
            "event_id": event_id,
            "captured_at": captured.isoformat(),
            "kind": "thumb",
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r


async def sightings_by_event(client) -> dict[str, str]:
    page = (await client.get("/api/v1/photos?limit=200")).json()
    return {p["event_id"]: p["sighting_id"] for p in page["items"]}


async def test_burst_shares_sighting_and_gap_splits(client, device_token):
    base = utcnow() - timedelta(hours=2)
    for i in range(3):
        await ingest(client, device_token, f"burst-{i}", base + timedelta(seconds=10 * i))
    await ingest(client, device_token, "later", base + timedelta(minutes=40))

    by_event = await sightings_by_event(client)
    assert by_event["burst-0"] == by_event["burst-1"] == by_event["burst-2"]
    assert by_event["later"] != by_event["burst-0"]


async def test_cameras_never_share_a_sighting(client, device_token):
    at = utcnow() - timedelta(hours=1)
    await ingest(client, device_token, "cam-a", at, camera="cam-a")
    await ingest(client, device_token, "cam-b", at, camera="cam-b")
    by_event = await sightings_by_event(client)
    assert by_event["cam-a"] != by_event["cam-b"]


async def test_straggler_bridges_two_sightings(client, device_token):
    """Store-and-forward: the middle frame of a visit arrives LAST, landing
    between two groups that formed on their own — they merge into one."""
    base = utcnow() - timedelta(hours=3)
    await ingest(client, device_token, "early", base)
    await ingest(client, device_token, "late", base + timedelta(minutes=50))
    by_event = await sightings_by_event(client)
    assert by_event["early"] != by_event["late"]  # 50 min apart: two visits so far

    await ingest(client, device_token, "middle", base + timedelta(minutes=25))
    by_event = await sightings_by_event(client)
    assert by_event["early"] == by_event["middle"] == by_event["late"]


async def test_full_arrival_keeps_the_sighting(client, device_token):
    at = utcnow() - timedelta(hours=1)
    await ingest(client, device_token, "evt", at)
    before = (await sightings_by_event(client))["evt"]
    r = await client.post(
        "/api/v1/ingest",
        data={
            "site": "north40",
            "camera": "c3-back-of-lake",
            "event_id": "evt",
            "captured_at": at.isoformat(),
            "kind": "full",
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200
    assert (await sightings_by_event(client))["evt"] == before


# --- grouped feed: /sightings ------------------------------------------------


async def test_sightings_feed_groups_counts_and_covers(client, device_token):
    base = utcnow() - timedelta(hours=6)
    for i in range(3):  # visit 1
        await ingest(client, device_token, f"v1-{i}", base + timedelta(seconds=30 * i))
    await ingest(client, device_token, "v2-0", base + timedelta(hours=2))  # visit 2
    await ingest(client, device_token, "other", base, camera="cam-b")  # other camera

    page = (await client.get("/api/v1/sightings")).json()
    assert [s["count"] for s in page["items"]] == [1, 1, 3]  # newest arrival first
    v1 = page["items"][2]
    assert v1["cover"]["event_id"] == "v1-0"  # earliest frame opens the burst
    assert v1["started_at"] < v1["ended_at"]
    assert page["next_cursor"] is None


async def test_sightings_pagination_disjoint(client, device_token):
    base = utcnow() - timedelta(hours=12)
    for i in range(3):
        await ingest(client, device_token, f"s{i}", base + timedelta(hours=2 * i))
    p1 = (await client.get("/api/v1/sightings?limit=2")).json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = (await client.get(f"/api/v1/sightings?limit=2&before={p1['next_cursor']}")).json()
    ids1 = {s["id"] for s in p1["items"]}
    assert ids1.isdisjoint({s["id"] for s in p2["items"]})
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None


async def test_sightings_filter_then_group(client, device_token):
    """kept=1: the sighting shows its matching frames only — count, cover,
    and kept_count all describe the kept subset."""
    base = utcnow() - timedelta(hours=1)
    for i in range(3):
        await ingest(client, device_token, f"k{i}", base + timedelta(seconds=10 * i))
    photos = (await client.get("/api/v1/photos")).json()["items"]
    keep_me = next(p for p in photos if p["event_id"] == "k1")
    await client.post(f"/api/v1/photos/{keep_me['id']}/keep", json={"keep": True})

    all_page = (await client.get("/api/v1/sightings")).json()["items"]
    assert all_page[0]["count"] == 3 and all_page[0]["kept_count"] == 1

    kept_page = (await client.get("/api/v1/sightings?kept=1")).json()["items"]
    assert len(kept_page) == 1
    assert kept_page[0]["count"] == 1
    assert kept_page[0]["cover"]["event_id"] == "k1"


async def test_sighting_photos_chronological_and_404(client, device_token):
    base = utcnow() - timedelta(hours=1)
    # Arrival order scrambled on purpose; playback must be capture order.
    await ingest(client, device_token, "f1", base + timedelta(seconds=20))
    await ingest(client, device_token, "f0", base)
    await ingest(client, device_token, "f2", base + timedelta(seconds=40))

    sid = (await client.get("/api/v1/sightings")).json()["items"][0]["id"]
    frames = (await client.get(f"/api/v1/sightings/{sid}/photos")).json()
    assert [f["event_id"] for f in frames] == ["f0", "f1", "f2"]

    import uuid as _uuid

    r = await client.get(f"/api/v1/sightings/{_uuid.uuid4()}/photos")
    assert r.status_code == 404


# --- /photos/histogram ---------------------------------------------------------


async def test_histogram_hourly_counts_and_filters(client, device_token):
    base = (utcnow() - timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    await ingest(client, device_token, "h0", base + timedelta(minutes=5))
    await ingest(client, device_token, "h1", base + timedelta(minutes=25))
    await ingest(client, device_token, "h2", base + timedelta(hours=3), camera="cam-b")

    buckets = (await client.get("/api/v1/photos/histogram")).json()
    assert [b["count"] for b in buckets] == [2, 1]

    cam_b = (await client.get("/api/v1/cameras?site=north40")).json()
    cam_b_id = next(c["id"] for c in cam_b if c["slug"] == "cam-b")
    only_b = (await client.get(f"/api/v1/photos/histogram?camera_id={cam_b_id}")).json()
    assert len(only_b) == 1 and only_b[0]["count"] == 1


# --- migration 0009 backfill: frozen gap walk over existing rows -------------


def _load_migration():
    path = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0009_sightings.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_backfill_groups_by_camera_and_gap(client, device_token):
    base = utcnow() - timedelta(days=1)
    # camera A: pair, 40-min gap, singleton; camera B: one frame inside A's window
    await ingest(client, device_token, "a-0", base, camera="cam-a")
    await ingest(client, device_token, "a-1", base + timedelta(minutes=5), camera="cam-a")
    await ingest(client, device_token, "a-2", base + timedelta(minutes=45), camera="cam-a")
    await ingest(client, device_token, "b-0", base + timedelta(minutes=2), camera="cam-b")

    # Scramble the ingest-assigned groups so the backfill starts from chaos
    # (pre-0009 rows have no groups at all; the walk rewrites everything).
    async with get_sessionmaker()() as session:
        for pid in (await session.scalars(select(Photo.id))).all():
            await session.execute(
                update(Photo).where(Photo.id == pid).values(sighting_id=uuid.uuid4())
            )
        await session.commit()

    migration = _load_migration()
    async with get_engine().begin() as conn:
        await conn.run_sync(migration._backfill)

    by_event = await sightings_by_event(client)
    assert by_event["a-0"] == by_event["a-1"]
    assert by_event["a-2"] != by_event["a-0"]
    assert by_event["b-0"] not in (by_event["a-0"], by_event["a-2"])


async def test_sightings_anchor_partitions_and_pages_upward(client, device_token):
    """Scrubber paging on the grouped feed: anchor splits on each sighting's
    newest arrival, direction=newer walks back toward now, no overlap."""
    base = utcnow() - timedelta(hours=5)
    for i in range(4):  # 4 visits an hour apart -> 4 sightings, arrival-ordered
        await ingest(client, device_token, f"visit-{i}", base + timedelta(hours=i))

    top = (await client.get("/api/v1/sightings")).json()["items"]
    assert len(top) == 4
    anchor = top[1]["last_received_at"]

    down = (await client.get(f"/api/v1/sightings?anchor={anchor}&limit=2")).json()
    up = (await client.get(f"/api/v1/sightings?anchor={anchor}&direction=newer&limit=1")).json()
    assert [s["id"] for s in down["items"]] == [top[2]["id"], top[3]["id"]]
    assert [s["id"] for s in up["items"]] == [top[1]["id"]]

    up2 = (await client.get(f"/api/v1/sightings?after={up['next_cursor']}&limit=5")).json()
    assert [s["id"] for s in up2["items"]] == [top[0]["id"]]
    assert up2["next_cursor"] is None
