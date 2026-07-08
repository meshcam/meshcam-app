import uuid

from trailcam.db import get_sessionmaker
from trailcam.models import Camera, Site, utcnow

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


async def ingest_one(client, token, event_id="evt-r1"):
    return await client.post(
        "/api/v1/ingest",
        data={
            "site": "north40",
            "camera": "c3-back-of-lake",
            "event_id": event_id,
            "captured_at": utcnow().isoformat(),
            "kind": "thumb",
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_get_single_photo(client, device_token):
    await ingest_one(client, device_token)
    listed = (await client.get("/api/v1/photos")).json()["items"][0]
    single = (await client.get(f"/api/v1/photos/{listed['id']}")).json()
    assert single == listed
    assert (await client.get(f"/api/v1/photos/{uuid.uuid4()}")).status_code == 404


async def test_photo_ref_accepts_uuid_variants_and_prefixes(client, device_token):
    await ingest_one(client, device_token)
    canonical = (await client.get("/api/v1/photos")).json()["items"][0]["id"]
    hexstr = canonical.replace("-", "")

    for ref in (canonical, hexstr, hexstr.upper(), hexstr[:12]):
        r = await client.get(f"/api/v1/photos/{ref}")
        assert r.status_code == 200, ref
        assert r.json()["id"] == canonical

    # below the 8-hex-char floor a prefix is rejected, not resolved
    assert (await client.get(f"/api/v1/photos/{hexstr[:6]}")).status_code == 404


async def test_camera_filter_accepts_uuid_and_prefix(client, device_token):
    await ingest_one(client, device_token)
    cam_id = (await client.get("/api/v1/cameras")).json()[0]["id"]

    for ref in (cam_id, cam_id.replace("-", "")[:10]):
        page = (await client.get(f"/api/v1/photos?camera_id={ref}")).json()
        assert len(page["items"]) == 1, ref
    assert (await client.get("/api/v1/photos?camera_id=deadbeefdead")).status_code == 404


async def test_ambiguous_node_prefix_is_a_conflict(client):
    async with get_sessionmaker()() as session:
        site = Site(slug="s", name="S")
        session.add(site)
        await session.flush()
        session.add_all(
            [
                Camera(
                    site_id=site.id,
                    slug="a",
                    name="A",
                    public_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                ),
                Camera(
                    site_id=site.id,
                    slug="b",
                    name="B",
                    public_id=uuid.UUID("11111111-1111-1111-1111-222222222222"),
                ),
            ]
        )
        await session.commit()

    assert (await client.get("/api/v1/nodes/11111111/telemetry")).status_code == 409
    # a longer, unambiguous prefix resolves
    r = await client.get("/api/v1/nodes/111111111111111111112/telemetry")
    assert r.status_code == 200


async def test_spa_fallback_serves_index_for_client_routes(client):
    for path in ("/", "/photos/abc-123", "/nodes", "/nodes/4?range=7d"):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert "trailcam-test-spa" in r.text, path
        # index.html is the deploy pointer — browsers must revalidate it
        assert r.headers["cache-control"] == "no-cache", path


async def test_spa_fallback_does_not_mask_api_404s(client, device_token):
    assert (await client.get("/api/v1/nope")).status_code == 404
    assert (await client.get(f"/api/v1/photos/{uuid.uuid4()}")).status_code == 404
