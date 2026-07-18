from datetime import timedelta

from sqlalchemy import update

from trailcam.db import get_sessionmaker
from trailcam.models import Photo, utcnow
from trailcam.purge import purge_once

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


async def ingest(client, token, event_id="evt-1", kind="thumb", meta=None, captured=None):
    captured = captured or utcnow()
    data = {
        "site": "north40",
        "camera": "c3-back-of-lake",
        "event_id": event_id,
        "captured_at": captured.isoformat(),
        "kind": kind,
    }
    if meta:
        data["meta"] = meta
    return await client.post(
        "/api/v1/ingest",
        data=data,
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_ingest_requires_device_token(client):
    r = await client.post("/api/v1/ingest", data={}, files={"file": ("x.jpg", JPEG)})
    assert r.status_code == 401


async def test_ingest_thumb_then_full_one_photo(client, device_token, store):
    r = await ingest(client, device_token, meta='{"battery_v": 3.31, "rssi": -97}')
    assert r.status_code == 200, r.text
    assert r.json()["stored"] == "thumb"

    r = await ingest(client, device_token, kind="full")
    assert r.status_code == 200

    page = (await client.get("/api/v1/photos")).json()
    assert len(page["items"]) == 1
    photo = page["items"][0]
    assert photo["has_full"] is True
    assert photo["keep"] is False
    assert photo["expires_at"] is not None
    assert photo["meta"]["battery_v"] == 3.31
    assert photo["site_slug"] == "north40"
    assert len(store.objects) == 2

    cams = (await client.get("/api/v1/cameras?site=north40")).json()
    assert cams[0]["last_battery_v"] == 3.31


async def test_image_streams_bytes(client, device_token):
    await ingest(client, device_token)
    pid = (await client.get("/api/v1/photos")).json()["items"][0]["id"]
    r = await client.get(f"/api/v1/photos/{pid}/image?size=thumb")
    assert r.status_code == 200
    assert r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"


async def test_keep_clears_expiry_and_unkeep_restores(client, device_token):
    await ingest(client, device_token)
    pid = (await client.get("/api/v1/photos")).json()["items"][0]["id"]

    kept = (await client.post(f"/api/v1/photos/{pid}/keep", json={"keep": True})).json()
    assert kept["keep"] is True and kept["expires_at"] is None

    unkept = (await client.post(f"/api/v1/photos/{pid}/keep", json={"keep": False})).json()
    assert unkept["keep"] is False and unkept["expires_at"] is not None


async def test_purge_deletes_expired_but_not_kept(client, device_token, store):
    await ingest(client, device_token, event_id="expired")
    await ingest(client, device_token, event_id="kept")
    page = (await client.get("/api/v1/photos")).json()["items"]
    kept_id = page[0]["id"]

    # Mark one kept, then force every un-kept photo past its TTL at the DB level.
    await client.post(f"/api/v1/photos/{kept_id}/keep", json={"keep": True})
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Photo)
            .where(Photo.keep.is_(False))
            .values(expires_at=utcnow() - timedelta(days=1))
        )
        await session.commit()

    purged = await purge_once()
    assert purged == 1

    remaining = (await client.get("/api/v1/photos")).json()["items"]
    assert [p["id"] for p in remaining] == [kept_id]
    assert len(store.objects) == 1  # expired photo's object removed


async def test_pagination_cursor_orders_by_arrival(client, device_token):
    base = utcnow()
    for i in range(5):
        await ingest(client, device_token, event_id=f"e{i}", captured=base - timedelta(minutes=i))
    p1 = (await client.get("/api/v1/photos?limit=2")).json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = (await client.get(f"/api/v1/photos?limit=2&before={p1['next_cursor']}")).json()
    ids1 = {p["id"] for p in p1["items"]}
    assert ids1.isdisjoint({p["id"] for p in p2["items"]})
    # newest-ARRIVAL-first: last ingested (e4) leads, regardless of captured_at
    assert p1["items"][0]["received_at"] > p2["items"][0]["received_at"]


async def test_anchor_partitions_and_pages_upward(client, device_token):
    base = utcnow()
    for i in range(6):
        await ingest(client, device_token, event_id=f"a{i}", captured=base - timedelta(minutes=i))
    top = (await client.get("/api/v1/photos")).json()["items"]  # newest arrival first
    anchor = top[2]["received_at"]

    down = (await client.get(f"/api/v1/photos?anchor={anchor}&limit=2")).json()
    up = (await client.get(f"/api/v1/photos?anchor={anchor}&direction=newer&limit=2")).json()
    # Clean partition: down is strictly older than the anchor, up is the anchor
    # and newer — both still newest-first.
    assert [p["id"] for p in down["items"]] == [top[3]["id"], top[4]["id"]]
    assert [p["id"] for p in up["items"]] == [top[1]["id"], top[2]["id"]]

    up2 = (await client.get(f"/api/v1/photos?after={up['next_cursor']}&limit=5")).json()
    assert [p["id"] for p in up2["items"]] == [top[0]["id"]]
    assert up2["next_cursor"] is None


async def test_future_captured_at_clamped(client, device_token):
    future = utcnow() + timedelta(hours=4)  # leaf clock running fast (seen on bench)
    r = await ingest(client, device_token, event_id="fastclock", captured=future)
    assert r.status_code == 200, r.text
    photo = (await client.get("/api/v1/photos")).json()["items"][0]
    assert photo["captured_at"] <= photo["received_at"]
    assert photo["meta"]["reported_captured_at"] == future.isoformat()

async def test_captured_window_filters(client, device_token):
    base = utcnow() - timedelta(days=3)
    for i in range(3):
        await ingest(client, device_token, event_id=f"day-{i}", captured=base + timedelta(days=i))

    day1 = base + timedelta(days=1)
    r = await client.get(
        "/api/v1/photos",
        params={
            "captured_after": day1.isoformat(),
            "captured_before": (day1 + timedelta(days=1)).isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    page = r.json()
    assert len(page["items"]) == 1
    assert page["items"][0]["captured_at"].startswith(day1.date().isoformat())
