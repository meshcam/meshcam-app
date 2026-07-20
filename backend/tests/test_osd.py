"""OSD watermark (trailcam.osd) — the bar itself, and the ingest wiring:
fulls are stamped, pristine bytes live at the .raw sibling key, ?size=raw is
the out-of-the-way door, and an undecodable frame is stored untouched."""

from datetime import UTC, datetime
from io import BytesIO

from PIL import Image

from test_ingest_and_gallery import ingest
from trailcam import osd
from trailcam.models import utcnow
from trailcam.s3 import raw_variant

CAPTURED = datetime(2026, 7, 3, 5, 42, 17, tzinfo=UTC)


def white_jpeg(w: int = 640, h: int = 480, mode: str = "RGB") -> bytes:
    buf = BytesIO()
    color = 250 if mode == "L" else (250, 250, 250)
    Image.new(mode, (w, h), color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def bar_is_drawn(jpeg: bytes) -> bool:
    im = Image.open(BytesIO(jpeg)).convert("RGB")
    r, g, b = im.getpixel((2, im.height - 2))
    return (r + g + b) / 3 < 60  # near-black bar over a near-white frame


def test_stamp_draws_bar_and_keeps_dimensions():
    src = white_jpeg()
    out = osd.stamp(
        src, camera_label="Oak Flats", captured_at=CAPTURED, temp_c=14.2, battery_v=3.41
    )
    assert out != src
    im = Image.open(BytesIO(out))
    assert im.format == "JPEG"
    assert im.size == (640, 480)
    assert bar_is_drawn(out)
    assert not bar_is_drawn(src)


def test_stamp_grayscale_ir_frame():
    out = osd.stamp(white_jpeg(mode="L"), camera_label="Beech Ridge", captured_at=CAPTURED)
    assert Image.open(BytesIO(out)).mode == "L"
    assert bar_is_drawn(out)


def test_stamp_skips_tiny_frames():
    src = white_jpeg(w=160, h=120)
    assert osd.stamp(src, camera_label="x", captured_at=CAPTURED) == src


def test_stamp_thumbnail_upscales_before_stamping():
    """A 320px detector crop upscales to _STAMP_MIN_W before the bar is drawn,
    so the text renders from enough pixels to survive the gallery's blow-up."""
    out = osd.stamp(
        white_jpeg(w=320, h=240),
        camera_label="Creek Crossing",
        captured_at=CAPTURED,
        temp_c=14.2,
        battery_v=3.41,
    )
    assert bar_is_drawn(out)
    assert Image.open(BytesIO(out)).size == (640, 480)


def test_stamp_upscale_capped_at_3x():
    out = osd.stamp(white_jpeg(w=200, h=150), camera_label="x", captured_at=CAPTURED)
    assert Image.open(BytesIO(out)).size == (600, 450)


def test_stamp_bad_timezone_falls_back_to_utc():
    out = osd.stamp(
        white_jpeg(), camera_label="x", captured_at=CAPTURED, tz="Not/AZone"
    )
    assert bar_is_drawn(out)


def test_looks_ir():
    assert osd.looks_ir(Image.new("L", (64, 64), 120))
    assert osd.looks_ir(Image.new("RGB", (64, 64), (120, 120, 120)))
    assert not osd.looks_ir(Image.new("RGB", (64, 64), (180, 120, 60)))


def test_battery_pct_bounds():
    assert osd.battery_pct(3.5) == 100
    assert osd.battery_pct(2.5) == 5
    assert osd.battery_pct(3.25) == 50


async def test_full_ingest_stamps_and_keeps_raw(client, device_token, store):
    original = white_jpeg()
    r = await ingest(client, device_token, meta='{"battery_v": 3.31, "temp_c": 18.5}')
    assert r.status_code == 200
    r = await client.post(
        "/api/v1/ingest",
        data={
            "site": "north40",
            "camera": "c3-back-of-lake",
            "event_id": "evt-1",
            "captured_at": utcnow().isoformat(),
            "kind": "full",
        },
        files={"file": ("x.jpg", original, "image/jpeg")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200

    full_key = next(k for k in store.objects if k.endswith(".full.jpg"))
    raw_key = raw_variant(full_key)
    assert raw_key in store.objects
    assert store.objects[raw_key] == original
    assert bar_is_drawn(store.objects[full_key])

    photo = (await client.get("/api/v1/photos")).json()["items"][0]
    assert photo["full_size"] == len(store.objects[full_key])

    # the stamped frame is the default full; raw is the out-of-the-way door
    full = await client.get(f"/api/v1/photos/{photo['id']}/image?size=full")
    assert bar_is_drawn(full.content)
    raw = await client.get(f"/api/v1/photos/{photo['id']}/image?size=raw")
    assert raw.content == original


async def test_thumb_ingest_stamped_without_raw_twin(client, device_token, store):
    """Thumbs get the bar too (a deep node's thumb may be the only delivery),
    but no .raw twin — the full is their original."""
    r = await client.post(
        "/api/v1/ingest",
        data={
            "site": "north40",
            "camera": "c3-back-of-lake",
            "event_id": "evt-thumb",
            "captured_at": utcnow().isoformat(),
            "kind": "thumb",
            "meta": '{"battery_v": 3.31, "temp_c": 18.5}',
        },
        files={"file": ("x.jpg", white_jpeg(w=320, h=240), "image/jpeg")},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200
    thumb_key = next(k for k in store.objects if k.endswith(".thumb.jpg"))
    assert bar_is_drawn(store.objects[thumb_key])
    assert raw_variant(thumb_key) not in store.objects


async def test_undecodable_full_stored_unstamped(client, device_token, store):
    await ingest(client, device_token, event_id="evt-2")
    r = await ingest(client, device_token, event_id="evt-2", kind="full")
    assert r.status_code == 200
    full_key = next(k for k in store.objects if k.endswith(".full.jpg"))
    assert raw_variant(full_key) not in store.objects  # fallback path: no raw twin


async def test_raw_falls_back_to_full_for_pre_osd_photos(client, device_token, store):
    """Fulls stored before the OSD feature have no .raw twin — serve the full."""
    await ingest(client, device_token, event_id="evt-3")
    await ingest(client, device_token, event_id="evt-3", kind="full")  # fake bytes: no raw
    pid = (await client.get("/api/v1/photos")).json()["items"][0]["id"]
    r = await client.get(f"/api/v1/photos/{pid}/image?size=raw")
    assert r.status_code == 200
    full_key = next(k for k in store.objects if k.endswith(".full.jpg"))
    assert r.content == store.objects[full_key]


def legacy_stamp(jpeg: bytes, **kw) -> bytes:
    """Stamp at native resolution — how pre-upscale thumbs were produced."""
    saved = osd._STAMP_MIN_W
    osd._STAMP_MIN_W = 0
    try:
        return osd.stamp(jpeg, **kw)
    finally:
        osd._STAMP_MIN_W = saved


def test_restamp_covers_legacy_bar():
    legacy = legacy_stamp(
        white_jpeg(w=320, h=240), camera_label="Lake Path", captured_at=CAPTURED
    )
    old_band = osd._bar_band(Image.open(BytesIO(legacy)))
    assert old_band > 0
    out = osd.restamp(legacy, camera_label="Lake Path", captured_at=CAPTURED)
    assert out is not None
    im = Image.open(BytesIO(out))
    assert im.size == (640, 480)
    assert bar_is_drawn(out)
    # the fresh bar fully covers the old band (scaled 2x by the upscale)
    assert osd._bar_band(im) >= old_band * 2


def test_restamp_skips_wide_unstamped_and_restamped():
    wide = osd.stamp(white_jpeg(), camera_label="x", captured_at=CAPTURED)
    assert osd.restamp(wide, camera_label="x", captured_at=CAPTURED) is None
    # no bar band on an unstamped frame -> left alone
    assert osd.restamp(white_jpeg(w=320, h=240), camera_label="x", captured_at=CAPTURED) is None
    # idempotent: a restamped frame is wide now
    legacy = legacy_stamp(white_jpeg(w=320, h=240), camera_label="x", captured_at=CAPTURED)
    once = osd.restamp(legacy, camera_label="x", captured_at=CAPTURED)
    assert osd.restamp(once, camera_label="x", captured_at=CAPTURED) is None


async def test_migration_0011_restamps_legacy_thumbs(client, device_token, store):
    """The 0011 backfill: a legacy-stamped narrow thumb in the store comes out
    640 wide with the bar redrawn, and reports its new size."""
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import select

    from trailcam.db import get_sessionmaker
    from trailcam.models import Camera, Photo

    saved = osd._STAMP_MIN_W
    osd._STAMP_MIN_W = 0
    try:
        r = await client.post(
            "/api/v1/ingest",
            data={
                "site": "north40",
                "camera": "c3-back-of-lake",
                "event_id": "legacy-1",
                "captured_at": CAPTURED.isoformat(),
                "kind": "thumb",
            },
            files={"file": ("x.jpg", white_jpeg(w=320, h=240), "image/jpeg")},
            headers={"Authorization": f"Bearer {device_token}"},
        )
        assert r.status_code == 200, r.text
    finally:
        osd._STAMP_MIN_W = saved

    path = Path(__file__).parent.parent / "alembic" / "versions" / "0011_restamp_thumbs.py"
    spec = importlib.util.spec_from_file_location("migration_0011", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    async with get_sessionmaker()() as session:
        photo = (await session.scalars(select(Photo))).one()
        cam = (await session.scalars(select(Camera).where(Camera.id == photo.camera_id))).one()
    assert Image.open(BytesIO(store.objects[photo.thumb_key])).width == 320

    rows = [
        SimpleNamespace(
            id=photo.id,
            thumb_key=photo.thumb_key,
            captured_at=photo.captured_at,
            meta=photo.meta,
            name=cam.name,
        )
    ]
    done = await mod._restamp_all(rows)
    assert len(done) == 1
    restamped = store.objects[photo.thumb_key]
    assert Image.open(BytesIO(restamped)).width == 640
    assert bar_is_drawn(restamped)
    assert done[0][1] == len(restamped)
