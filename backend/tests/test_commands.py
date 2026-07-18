from datetime import timedelta

from sqlalchemy import update

from trailcam.db import get_sessionmaker
from trailcam.models import Command, utcnow
from trailcam.purge import expire_commands

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


async def ingest(client, token, event_id="evt-1", kind="thumb"):
    return await client.post(
        "/api/v1/ingest",
        data={
            "site": "north40",
            "camera": "c3-back-of-lake",
            "event_id": event_id,
            "captured_at": utcnow().isoformat(),
            "kind": kind,
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )


async def first_photo(client):
    return (await client.get("/api/v1/photos")).json()["items"][0]


async def test_request_full_queues_once(client, device_token):
    await ingest(client, device_token)
    photo = await first_photo(client)
    assert photo["full_requested"] is False

    r = (await client.post(f"/api/v1/photos/{photo['id']}/request-full")).json()
    assert r["full_requested"] is True and r["has_full"] is False

    # idempotent — second request doesn't queue a second command
    await client.post(f"/api/v1/photos/{photo['id']}/request-full")
    cmds = (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json()
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd["kind"] == "fetch_full"
    assert cmd["node"] == "c3-back-of-lake"
    assert cmd["site"] == "north40"
    assert cmd["event_id"] == "evt-1"
    assert cmd["status"] == "delivered"  # poll marks delivery

    # still returned on the next poll (re-delivery until done)
    cmds2 = (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json()
    assert [c["id"] for c in cmds2] == [cmd["id"]]

    # list keeps exposing the outstanding request
    assert (await first_photo(client))["full_requested"] is True


async def test_full_ingest_completes_command(client, device_token):
    await ingest(client, device_token)
    photo = await first_photo(client)
    await client.post(f"/api/v1/photos/{photo['id']}/request-full")

    await ingest(client, device_token, kind="full")

    cmds = (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json()
    assert cmds == []
    photo = await first_photo(client)
    assert photo["has_full"] is True and photo["full_requested"] is False


async def test_commands_require_device_token(client, device_token):
    assert (await client.get("/api/v1/commands")).status_code == 401


async def test_ack_failed(client, device_token):
    await ingest(client, device_token)
    photo = await first_photo(client)
    await client.post(f"/api/v1/photos/{photo['id']}/request-full")
    cmd = (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json()[0]
    r = (
        await client.post(
            f"/api/v1/commands/{cmd['id']}/ack",
            json={"status": "failed", "detail": "node unreachable"},
            headers={"Authorization": f"Bearer {device_token}"},
        )
    ).json()
    assert r["status"] == "failed"
    assert (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json() == []


async def test_ack_received_stops_redelivery_but_still_completes(client, device_token):
    """Bug 8 (2026-07-16): 'delivered' only means the gateway fetched it — commands sat
    delivered for hours while TXed onto a dead radio profile. The leaf now acks receipt
    via its announce; received stops redelivery but the fetch_full still finishes via
    the kind=full ingest, and a late receipt ack never regresses a finished command."""
    await ingest(client, device_token)
    photo = await first_photo(client)
    await client.post(f"/api/v1/photos/{photo['id']}/request-full")
    headers = {"Authorization": f"Bearer {device_token}"}
    cmd = (await client.get("/api/v1/commands", headers=headers)).json()[0]

    r = (
        await client.post(
            f"/api/v1/commands/{cmd['id']}/ack",
            json={"status": "received", "detail": "leaf ack via announce"},
            headers=headers,
        )
    ).json()
    assert r["status"] == "received"

    # received -> no longer redelivered ...
    assert (await client.get("/api/v1/commands", headers=headers)).json() == []
    # ... but still in flight for the UI (photo shows the outstanding request) ...
    assert (await first_photo(client))["full_requested"] is True
    diag = (await client.get(f"/api/v1/photos/{photo['id']}/full-request")).json()
    assert diag["status"] == "received"
    assert diag["received_at"] is not None

    # ... and the kind=full ingest still completes it.
    await ingest(client, device_token, kind="full")
    photo = await first_photo(client)
    assert photo["has_full"] is True and photo["full_requested"] is False

    # A late/duplicate receipt ack must not regress the finished command.
    r = (
        await client.post(
            f"/api/v1/commands/{cmd['id']}/ack",
            json={"status": "received"},
            headers=headers,
        )
    ).json()
    assert r["status"] == "done"


async def test_stale_commands_expire(client, device_token):
    await ingest(client, device_token)
    photo = await first_photo(client)
    await client.post(f"/api/v1/photos/{photo['id']}/request-full")
    async with get_sessionmaker()() as session:
        await session.execute(update(Command).values(created_at=utcnow() - timedelta(days=30)))
        await session.commit()
    assert await expire_commands() == 1
    assert (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json() == []
    assert (await first_photo(client))["full_requested"] is False

async def test_full_request_diagnostics(client, device_token):
    await ingest(client, device_token)
    photo = await first_photo(client)

    # no request yet
    assert (await client.get(f"/api/v1/photos/{photo['id']}/full-request")).status_code == 404

    await client.post(f"/api/v1/photos/{photo['id']}/request-full")
    diag = (await client.get(f"/api/v1/photos/{photo['id']}/full-request")).json()
    assert diag["status"] == "pending"
    assert diag["delivered_at"] is None
    assert diag["requested_by"] == "test@example.com"
    assert diag["node_last_seen_at"] is not None

    # gateway polls -> delivered
    await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    diag = (await client.get(f"/api/v1/photos/{photo['id']}/full-request")).json()
    assert diag["status"] == "delivered"
    assert diag["delivered_at"] is not None

    # full lands -> done with completed_at
    await ingest(client, device_token, kind="full")
    diag = (await client.get(f"/api/v1/photos/{photo['id']}/full-request")).json()
    assert diag["status"] == "done"
    assert diag["completed_at"] is not None


async def test_max_quality_request_after_standard_full(client, device_token):
    await ingest(client, device_token)
    await ingest(client, device_token, kind="full")
    photo = await first_photo(client)
    assert photo["has_full"] is True

    # standard re-request is a no-op (already satisfied)
    r = (await client.post(f"/api/v1/photos/{photo['id']}/request-full")).json()
    assert r["full_requested"] is False

    # max is allowed and carries quality in the command payload
    r = (
        await client.post(
            f"/api/v1/photos/{photo['id']}/request-full", json={"quality": "max"}
        )
    ).json()
    assert r["full_requested"] is True
    cmds = (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json()
    assert cmds[0]["payload"] == {"quality": "max"}

    diag = (await client.get(f"/api/v1/photos/{photo['id']}/full-request")).json()
    assert diag["quality"] == "max"
    assert diag["status"] == "delivered"

    # the max upload overwrites and completes
    await ingest(client, device_token, kind="full")
    diag = (await client.get(f"/api/v1/photos/{photo['id']}/full-request")).json()
    assert diag["status"] == "done"


async def test_feed_bus_events_drive_sse(client, device_token):
    """The /api/v1/events SSE endpoint is a pump over the FEED topic;
    ASGITransport can't stream infinite responses, so assert the bus layer:
    every transition publishes a fully resolved, correctly typed payload."""
    import asyncio

    from trailcam.events import FEED, bus

    async def drain(queue):
        out = []
        while True:
            try:
                out.append(await asyncio.wait_for(queue.get(), 0.3))
            except TimeoutError:
                return out

    async with bus.subscribe(FEED) as queue:
        # new photo -> feed 'photo' event with change=new
        await ingest(client, device_token)
        events = await drain(queue)
        assert [e["event"] for e in events] == ["photo"]
        assert events[0]["data"]["change"] == "new"
        assert events[0]["data"]["camera_name"] == "C3-BACK-OF-LAKE"
        photo_id = events[0]["data"]["id"]

        # request-full -> 'request' pending
        await client.post(f"/api/v1/photos/{photo_id}/request-full")
        events = await drain(queue)
        assert [e["event"] for e in events] == ["request"]
        assert events[0]["data"]["photo_id"] == photo_id
        assert events[0]["data"]["request"]["status"] == "pending"

        # gateway poll -> 'request' delivered
        await client.get(
            "/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"}
        )
        events = await drain(queue)
        assert events[0]["data"]["request"]["status"] == "delivered"

        # full ingest -> 'photo' updated + 'request' done
        await ingest(client, device_token, kind="full")
        events = await drain(queue)
        kinds = {e["event"] for e in events}
        assert kinds == {"photo", "request"}
        photo_ev = next(e for e in events if e["event"] == "photo")
        assert photo_ev["data"]["change"] == "updated"
        assert photo_ev["data"]["has_full"] is True
        req_ev = next(e for e in events if e["event"] == "request")
        assert req_ev["data"]["request"]["status"] == "done"

        # delete -> 'photo_removed'
        await client.delete(f"/api/v1/photos/{photo_id}")
        events = await drain(queue)
        assert [e["event"] for e in events] == ["photo_removed"]
        assert events[0]["data"]["id"] == photo_id


async def test_commands_wake_topic(client, device_token):
    """request-full wakes device command streams via the COMMANDS topic."""
    import asyncio

    from trailcam.events import COMMANDS, bus

    await ingest(client, device_token)
    photo = await first_photo(client)
    async with bus.subscribe(COMMANDS) as queue:
        await client.post(f"/api/v1/photos/{photo['id']}/request-full")
        await asyncio.wait_for(queue.get(), 2)  # wake signal arrived

async def test_node_command_history_redacts_psk(client, device_token):
    await ingest(client, device_token)
    node_id = (await client.get("/api/v1/nodes/health")).json()[0]["id"]

    r = await client.post(
        f"/api/v1/nodes/{node_id}/commands",
        json={
            "kind": "maintenance",
            "payload": {"ssid": "barn-wifi", "psk": "hunter2", "minutes": 15},
        },
    )
    assert r.status_code == 202

    history = (await client.get(f"/api/v1/nodes/{node_id}/commands")).json()
    assert [c["kind"] for c in history] == ["maintenance"]
    cmd = history[0]
    assert cmd["status"] == "pending"
    assert cmd["requested_by"] == "test@example.com"
    assert cmd["payload"]["ssid"] == "barn-wifi"
    assert cmd["payload"]["minutes"] == 15
    assert cmd["payload"]["psk"] == "••••"  # never echo WiFi creds back out

    # fetch_full commands show up in the same history.
    pid = (await first_photo(client))["id"]
    await client.post(f"/api/v1/photos/{pid}/request-full", json={"quality": "standard"})
    history = (await client.get(f"/api/v1/nodes/{node_id}/commands")).json()
    assert [c["kind"] for c in history] == ["fetch_full", "maintenance"]
