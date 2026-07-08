from trailcam.models import utcnow

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


async def ingest(client, token, site="home", camera="c1", event_id="evt-1"):
    return await client.post(
        "/api/v1/ingest",
        data={
            "site": site,
            "camera": camera,
            "event_id": event_id,
            "captured_at": utcnow().isoformat(),
            "kind": "thumb",
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )


# --- sites -------------------------------------------------------------------


async def test_site_rename_and_hide(client, device_token):
    await ingest(client, device_token, site="home", camera="c1", event_id="e1")
    await ingest(client, device_token, site="north40", camera="c2", event_id="e2")

    sites = (await client.get("/api/v1/sites")).json()
    assert [s["slug"] for s in sites] == ["home", "north40"]
    home = next(s for s in sites if s["slug"] == "home")

    r = await client.patch(f"/api/v1/sites/{home['id']}", json={"name": "The Farm"})
    assert r.status_code == 200
    assert r.json()["name"] == "The Farm"
    assert r.json()["slug"] == "home"  # slug is the ingest key; rename is display-only

    r = await client.patch(f"/api/v1/sites/{home['id']}", json={"hidden": True})
    assert r.json()["hidden"] is True

    visible = (await client.get("/api/v1/sites")).json()
    assert [s["slug"] for s in visible] == ["north40"]
    everything = (await client.get("/api/v1/sites?include_hidden=true")).json()
    assert [s["slug"] for s in everything] == ["home", "north40"]


async def test_camera_hide_filters_listing(client, device_token):
    await ingest(client, device_token, camera="c1", event_id="e1")
    await ingest(client, device_token, camera="c2", event_id="e2")

    cams = (await client.get("/api/v1/cameras")).json()
    c1 = next(c for c in cams if c["slug"] == "c1")

    r = await client.patch(
        f"/api/v1/cameras/{c1['id']}", json={"hidden": True, "notes": "retired 2026"}
    )
    assert r.status_code == 200
    assert r.json()["hidden"] is True
    assert r.json()["notes"] == "retired 2026"

    visible = (await client.get("/api/v1/cameras")).json()
    assert [c["slug"] for c in visible] == ["c2"]
    everything = (await client.get("/api/v1/cameras?include_hidden=true")).json()
    assert sorted(c["slug"] for c in everything) == ["c1", "c2"]


# --- device tokens -----------------------------------------------------------


async def test_device_token_lifecycle(client, store):
    r = await client.post("/api/v1/device-tokens", json={"name": "home-gateway"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["token"].startswith("tc_")
    assert created["last_used_at"] is None

    # Duplicate names conflict.
    r = await client.post("/api/v1/device-tokens", json={"name": "home-gateway"})
    assert r.status_code == 409

    # Listing never exposes the plaintext token.
    listed = (await client.get("/api/v1/device-tokens")).json()
    ours = next(t for t in listed if t["name"] == "home-gateway")
    assert "token" not in ours

    # The minted token actually authenticates ingest, which stamps last_used_at.
    r = await ingest(client, created["token"])
    assert r.status_code == 200
    listed = (await client.get("/api/v1/device-tokens")).json()
    ours = next(t for t in listed if t["name"] == "home-gateway")
    assert ours["last_used_at"] is not None

    # Revoke — the token stops working.
    r = await client.delete(f"/api/v1/device-tokens/{created['id']}")
    assert r.status_code == 204
    r = await ingest(client, created["token"], event_id="e2")
    assert r.status_code == 401


async def test_device_token_blank_name_rejected(client):
    r = await client.post("/api/v1/device-tokens", json={"name": "   "})
    assert r.status_code == 422


# --- retention stats -----------------------------------------------------------


async def test_retention_stats(client, device_token):
    await ingest(client, device_token, event_id="e1")
    await ingest(client, device_token, event_id="e2")
    pid = (await client.get("/api/v1/photos")).json()["items"][0]["id"]
    await client.post(f"/api/v1/photos/{pid}/keep", json={"keep": True})

    stats = (await client.get("/api/v1/retention")).json()
    assert stats["ttl_days"] == 30  # conftest sets TRAILCAM_PHOTO_TTL_DAYS=30
    assert stats["photo_count"] == 2
    assert stats["kept_count"] == 1
    assert stats["thumb_bytes"] == 2 * len(JPEG)
