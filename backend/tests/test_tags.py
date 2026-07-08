from trailcam.models import utcnow

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


async def ingest(client, token, event_id="evt-1"):
    return await client.post(
        "/api/v1/ingest",
        data={
            "site": "home",
            "camera": "c1",
            "event_id": event_id,
            "captured_at": utcnow().isoformat(),
            "kind": "thumb",
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_tag_add_remove_and_cleanup(client, device_token):
    await ingest(client, device_token)
    pid = (await client.get("/api/v1/photos")).json()["items"][0]["id"]

    r = await client.post(f"/api/v1/photos/{pid}/tags", json={"name": "Big Buck"})
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == [{"slug": "big-buck", "name": "Big Buck"}]

    # Re-adding (same slug, different casing) is a no-op.
    r = await client.post(f"/api/v1/photos/{pid}/tags", json={"name": "big buck"})
    assert len(r.json()["tags"]) == 1

    tags = (await client.get("/api/v1/tags")).json()
    assert tags == [{"slug": "big-buck", "name": "Big Buck", "count": 1}]

    r = await client.delete(f"/api/v1/photos/{pid}/tags/big-buck")
    assert r.status_code == 200
    assert r.json()["tags"] == []
    # Orphaned tag rows are dropped so the filter list stays tidy.
    assert (await client.get("/api/v1/tags")).json() == []


async def test_tag_feed_filter(client, device_token):
    await ingest(client, device_token, event_id="e1")
    await ingest(client, device_token, event_id="e2")
    ids = [p["id"] for p in (await client.get("/api/v1/photos")).json()["items"]]

    await client.post(f"/api/v1/photos/{ids[0]}/tags", json={"name": "deer"})
    await client.post(f"/api/v1/photos/{ids[1]}/tags", json={"name": "turkey"})

    page = (await client.get("/api/v1/photos?tag=deer")).json()
    assert [p["id"] for p in page["items"]] == [ids[0]]
    assert page["items"][0]["tags"][0]["slug"] == "deer"


async def test_tag_blank_rejected(client, device_token):
    await ingest(client, device_token)
    pid = (await client.get("/api/v1/photos")).json()["items"][0]["id"]
    r = await client.post(f"/api/v1/photos/{pid}/tags", json={"name": "  !! "})
    assert r.status_code == 422
