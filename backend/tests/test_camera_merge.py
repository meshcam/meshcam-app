"""Merging a re-identified node back onto its original camera row.

A leaf's wire slug is derived from its RNS identity. Lose the identity — a full
`esptool write-flash 0x0` writes 0xFF across the nvs partition, and
arduino-esp32 erases nvs by itself when it fills — and the same physical board
comes back announcing a different slug, auto-creating a second camera row. The
merge folds the two back together; the alias is what stops the node's very next
heartbeat from undoing it.
"""

from datetime import timedelta

from sqlalchemy import select

from trailcam.db import get_sessionmaker
from trailcam.models import Camera, Command, Photo, Telemetry, utcnow

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"

OLD = "leaf-0a8bd4e9"  # what board 0016 announced before the reflash
NEW = "leaf-34f258f3"  # what it announced after


async def ingest(client, token, node, event_id, captured=None):
    return await client.post(
        "/api/v1/ingest",
        data={
            "site": "north40",
            "camera": node,
            "event_id": event_id,
            "captured_at": (captured or utcnow()).isoformat(),
            "kind": "thumb",
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )


async def beat(client, token, node, **over):
    return await client.post(
        "/api/v1/telemetry",
        json={"site": "north40", "node": node, "kind": "camera", "battery_v": 3.3, **over},
        headers={"Authorization": f"Bearer {token}"},
    )


async def camera_by_slug(client, slug):
    for c in (await client.get("/api/v1/cameras?include_hidden=true")).json():
        if c["slug"] == slug:
            return c
    return None


async def test_reflash_splits_history_then_merge_reunites_it(client, device_token, store):
    """The whole bug and the whole fix, end to end."""
    old_when = utcnow() - timedelta(days=2)
    await ingest(client, device_token, OLD, "evt-before-1", old_when)
    await ingest(client, device_token, OLD, "evt-before-2", old_when + timedelta(seconds=30))
    await beat(client, device_token, OLD, battery_v=3.31)

    # ...board reflashed, identity regenerated, second row auto-created...
    await ingest(client, device_token, NEW, "evt-after-1")
    await beat(client, device_token, NEW, battery_v=3.28)

    cams = (await client.get("/api/v1/cameras")).json()
    assert {c["slug"] for c in cams} == {OLD, NEW}, "the split is the precondition"

    keep = await camera_by_slug(client, OLD)
    r = await client.post(
        f"/api/v1/cameras/{keep['id']}/merge",
        json={"source": NEW, "note": "board 0016 reflashed 2026-09-03"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["merged_slug"] == NEW
    assert out["aliased"] is True
    assert out["photos"] == 1
    assert out["telemetry"] == 1
    assert out["camera"]["slug"] == OLD
    assert out["camera"]["aliases"] == [NEW]

    cams = (await client.get("/api/v1/cameras")).json()
    assert [c["slug"] for c in cams] == [OLD], "the duplicate row is gone"

    # All three captures now hang off the surviving camera.
    node_id = cams[0]["id"]
    page = (await client.get(f"/api/v1/photos?camera={node_id}")).json()
    assert len(page["items"]) == 3

    # The kept row inherits the newer health, not the pre-reflash reading.
    assert cams[0]["last_battery_v"] == 3.28


async def test_alias_routes_future_traffic_to_the_kept_row(client, device_token, store):
    """Without this the node's next heartbeat re-creates what was merged."""
    await ingest(client, device_token, OLD, "evt-1")
    await ingest(client, device_token, NEW, "evt-2")
    keep = await camera_by_slug(client, OLD)
    await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW})

    # The board has no idea it was merged: it keeps announcing as NEW.
    await beat(client, device_token, NEW, battery_v=3.2)
    await ingest(client, device_token, NEW, "evt-3")

    cams = (await client.get("/api/v1/cameras")).json()
    assert [c["slug"] for c in cams] == [OLD], "no duplicate came back"
    page = (await client.get(f"/api/v1/photos?camera={cams[0]['id']}")).json()
    assert len(page["items"]) == 3


async def test_merge_moves_the_orphaned_command_queue(client, device_token, store):
    """The queued commands are the other half of the damage — they were
    addressed to a camera row the board no longer answers to."""
    await ingest(client, device_token, OLD, "evt-1")
    await ingest(client, device_token, NEW, "evt-2")
    new_cam = await camera_by_slug(client, NEW)

    async with get_sessionmaker()() as session:
        cam = await session.scalar(select(Camera).where(Camera.slug == NEW))
        session.add(Command(camera_id=cam.id, kind="maintenance", payload={"minutes": 15}))
        await session.commit()

    keep = await camera_by_slug(client, OLD)
    out = (
        await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": new_cam["id"]})
    ).json()
    assert out["commands"] == 1

    kept_id = (await camera_by_slug(client, OLD))["id"]
    async with get_sessionmaker()() as session:
        kept = await session.scalar(select(Camera).where(Camera.slug == OLD))
        cmds = (await session.scalars(select(Command))).all()
    assert kept_id  # the surviving row is the one the API reports
    assert len(cmds) == 1 and cmds[0].camera_id == kept.id


async def test_merge_regroups_sightings_across_the_split(client, device_token, store):
    """Two frames a minute apart are one animal visit — being logged under two
    camera rows never made them two."""
    base = utcnow() - timedelta(hours=1)
    await ingest(client, device_token, OLD, "evt-a", base)
    await ingest(client, device_token, NEW, "evt-b", base + timedelta(minutes=1))

    keep = await camera_by_slug(client, OLD)
    out = (await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW})).json()
    assert out["sightings"] == 1

    async with get_sessionmaker()() as session:
        sids = {p.sighting_id for p in (await session.scalars(select(Photo))).all()}
    assert len(sids) == 1


async def test_merge_source_accepts_a_uuid_ref_too(client, device_token, store):
    await ingest(client, device_token, OLD, "evt-1")
    await ingest(client, device_token, NEW, "evt-2")
    keep = await camera_by_slug(client, OLD)
    src = await camera_by_slug(client, NEW)
    r = await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": src["id"][:8]})
    assert r.status_code == 200, r.text
    assert r.json()["merged_slug"] == NEW


async def test_merge_refuses_self_and_unknown_and_cross_site(client, device_token, store):
    await ingest(client, device_token, OLD, "evt-1")
    keep = await camera_by_slug(client, OLD)

    r = await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": OLD})
    assert r.status_code == 409

    r = await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": "leaf-nope"})
    assert r.status_code == 404

    # A node of the same name at another site is a different node.
    await client.post(
        "/api/v1/telemetry",
        json={"site": "homestead", "node": NEW, "kind": "camera"},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    other = await camera_by_slug(client, NEW)
    r = await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": other["id"]})
    assert r.status_code == 409


async def test_merge_without_alias_lets_the_row_come_back(client, device_token, store):
    """alias=false is the "this really was two boards" case."""
    await ingest(client, device_token, OLD, "evt-1")
    await ingest(client, device_token, NEW, "evt-2")
    keep = await camera_by_slug(client, OLD)
    out = (
        await client.post(
            f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW, "alias": False}
        )
    ).json()
    assert out["aliased"] is False
    assert out["camera"]["aliases"] == []

    await beat(client, device_token, NEW)
    assert {c["slug"] for c in (await client.get("/api/v1/cameras")).json()} == {OLD, NEW}


async def test_deleting_an_alias_restores_autocreate(client, device_token, store):
    await ingest(client, device_token, OLD, "evt-1")
    await ingest(client, device_token, NEW, "evt-2")
    keep = await camera_by_slug(client, OLD)
    await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW})

    aliases = (await client.get(f"/api/v1/cameras/{keep['id']}/aliases")).json()
    assert [a["slug"] for a in aliases] == [NEW]

    r = await client.delete(f"/api/v1/cameras/{keep['id']}/aliases/{NEW}")
    assert r.status_code == 204
    assert (await client.delete(f"/api/v1/cameras/{keep['id']}/aliases/{NEW}")).status_code == 404

    await beat(client, device_token, NEW)
    assert {c["slug"] for c in (await client.get("/api/v1/cameras")).json()} == {OLD, NEW}


async def test_merging_twice_chains_both_old_slugs(client, device_token, store):
    """Board 0016 lost its identity twice in one day. Both retired slugs have
    to keep resolving, not just the most recent."""
    third = "leaf-93573b76"
    await ingest(client, device_token, OLD, "evt-1")
    await ingest(client, device_token, NEW, "evt-2")
    await ingest(client, device_token, third, "evt-3")

    keep = await camera_by_slug(client, OLD)
    await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW})
    out = (await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": third})).json()
    assert sorted(out["camera"]["aliases"]) == sorted([NEW, third])

    for slug in (NEW, third):
        await beat(client, device_token, slug)
    assert [c["slug"] for c in (await client.get("/api/v1/cameras")).json()] == [OLD]


async def test_merge_can_keep_the_new_row_instead(client, device_token, store):
    """Keeping the new identity and aliasing the old one is the same operation
    with the arguments swapped — useful when the new slug is already labelled."""
    await ingest(client, device_token, OLD, "evt-1")
    await ingest(client, device_token, NEW, "evt-2")
    keep = await camera_by_slug(client, NEW)
    out = (await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": OLD})).json()
    assert out["camera"]["slug"] == NEW
    assert out["camera"]["aliases"] == [OLD]

    # A straggler posted under the retired slug still lands on the kept row.
    await ingest(client, device_token, OLD, "evt-late")
    cams = (await client.get("/api/v1/cameras")).json()
    assert [c["slug"] for c in cams] == [NEW]
    page = (await client.get(f"/api/v1/photos?camera={cams[0]['id']}")).json()
    assert len(page["items"]) == 3


async def test_telemetry_rows_follow_the_merge(client, device_token):
    await beat(client, device_token, OLD, battery_v=3.31)
    await beat(client, device_token, NEW, battery_v=3.28)
    keep = await camera_by_slug(client, OLD)
    await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW})

    async with get_sessionmaker()() as session:
        rows = (await session.scalars(select(Telemetry))).all()
    assert len(rows) == 2
    node_id = (await camera_by_slug(client, OLD))["id"]
    series = (await client.get(f"/api/v1/nodes/{node_id}/telemetry?hours=24")).json()
    assert len(series["points"]) == 2


async def test_commands_address_the_slug_the_board_still_answers_to(client, device_token, store):
    """The outbound half of the same bug.

    The merge puts the history on one row, but the board has no idea and keeps
    announcing its new identity. Commands go out addressed by slug, and the
    gateway routes on the announce slug — so addressing the kept row's own slug
    sends every command to a name nothing answers to, which is the orphaned
    command queue this whole bead is about, re-created on the way out."""
    await ingest(client, device_token, OLD, "evt-1")
    await beat(client, device_token, NEW)
    keep = await camera_by_slug(client, OLD)
    await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW})

    # the board carries on under its new name
    await beat(client, device_token, NEW)

    async with get_sessionmaker()() as session:
        cam = await session.scalar(select(Camera).where(Camera.slug == OLD))
        session.add(Command(camera_id=cam.id, kind="maintenance", payload={"seconds": 30}))
        await session.commit()

    queued = (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json()
    assert len(queued) == 1
    assert queued[0]["node"] == NEW, "addressed to a slug no board answers to"


async def test_never_merged_camera_is_addressed_by_its_own_slug(client, device_token, store):
    await ingest(client, device_token, OLD, "evt-1")
    async with get_sessionmaker()() as session:
        cam = await session.scalar(select(Camera).where(Camera.slug == OLD))
        session.add(Command(camera_id=cam.id, kind="maintenance", payload={"seconds": 30}))
        await session.commit()
    queued = (
        await client.get("/api/v1/commands", headers={"Authorization": f"Bearer {device_token}"})
    ).json()
    assert queued[0]["node"] == OLD
