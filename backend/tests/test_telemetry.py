from datetime import timedelta

from sqlalchemy import update

from trailcam.db import get_sessionmaker
from trailcam.models import Telemetry, utcnow
from trailcam.purge import purge_telemetry

BEAT = {
    "site": "north40",
    "node": "r2-dam",
    "kind": "relay",
    "battery_v": 3.31,
    "temp_c": 22.4,
    "rssi": -88,
    "snr": 9.2,
    "uptime_s": 86400,
    "boot_reason": "POWERON",
    "fw_version": "relay-0.1.0",
}


async def post_beat(client, token, **over):
    return await client.post(
        "/api/v1/telemetry",
        json={**BEAT, **over},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_telemetry_requires_device_token(client):
    assert (await client.post("/api/v1/telemetry", json=BEAT)).status_code == 401


async def test_telemetry_autocreates_relay_and_updates_health(client, device_token):
    r = await post_beat(client, device_token)
    assert r.status_code == 200, r.text

    health = (await client.get("/api/v1/nodes/health")).json()
    assert len(health) == 1
    node = health[0]
    assert node["slug"] == "r2-dam"
    assert node["kind"] == "relay"
    assert node["site_slug"] == "north40"
    assert node["last_battery_v"] == 3.31
    assert node["last_seen_at"] is not None
    assert node["last_photo_at"] is None
    assert node["latest"]["snr"] == 9.2
    assert node["latest"]["boot_reason"] == "POWERON"


async def test_health_latest_is_most_recent_beat(client, device_token):
    await post_beat(client, device_token, battery_v=3.31)
    await post_beat(client, device_token, battery_v=3.28, boot_reason="DSLEEP")
    health = (await client.get("/api/v1/nodes/health")).json()
    assert health[0]["latest"]["battery_v"] == 3.28
    assert health[0]["latest"]["boot_reason"] == "DSLEEP"


async def test_telemetry_series_window(client, device_token):
    await post_beat(client, device_token)
    node_id = (await client.get("/api/v1/nodes/health")).json()[0]["id"]

    series = (await client.get(f"/api/v1/nodes/{node_id}/telemetry?hours=24")).json()
    assert len(series["points"]) == 1
    assert series["points"][0]["battery_v"] == 3.31

    # push one row outside the window at the DB level
    async with get_sessionmaker()() as session:
        await session.execute(update(Telemetry).values(received_at=utcnow() - timedelta(hours=48)))
        await session.commit()
    series = (await client.get(f"/api/v1/nodes/{node_id}/telemetry?hours=24")).json()
    assert series["points"] == []

    assert (await client.get("/api/v1/nodes/9999/telemetry")).status_code == 404


async def test_nodes_endpoints_require_session(app, client, device_token):
    await post_beat(client, device_token)
    from trailcam.auth import current_user

    app.dependency_overrides.pop(current_user)
    assert (await client.get("/api/v1/nodes/health")).status_code == 401


async def test_purge_telemetry_ages_out_old_rows(client, device_token):
    await post_beat(client, device_token)
    await post_beat(client, device_token, node="c3-back-of-lake", kind="camera")
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Telemetry)
            .where(Telemetry.snr == 9.2)
            .values(received_at=utcnow() - timedelta(days=400))
        )
        await session.commit()
    assert await purge_telemetry() == 2
    health = (await client.get("/api/v1/nodes/health")).json()
    assert {n["slug"] for n in health} == {"r2-dam", "c3-back-of-lake"}
    assert all(n["latest"] is None for n in health)
