"""Pins the whole /nodes/health payload so the query behind it can be rewritten.

The endpoint used to derive five different things from one unbounded pass over
the telemetry table. Each of those has a rule that is easy to lose in
a rewrite, so this seeds one node whose rows exercise all of them at once and
asserts the exact JSON:

  * `latest` is the newest row, with id breaking a received_at tie;
  * `health` is the newest row *inside* the 6 h window that carries one;
  * `push_failing` compares that to the MINIMUM push_fails in the window, so a
    reboot's counter reset self-adjusts;
  * `storage_state` comes from the newest windowed row carrying `sd`, which is
    not necessarily the row that carried `health`;
  * `prev_node` has deliberately NO window and is not read off `latest` — here
    it comes from a 30-day-old row, older than everything else seeded.

Plus a node whose `extra` is junk in every slot (none of it may be believed or
raise) and a node with no telemetry rows at all.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from trailcam.db import get_sessionmaker
from trailcam.models import Camera, Telemetry, utcnow

NOW = utcnow()
OLD_SLUG = "leaf-old"


async def seed(client, token, node, at, *, kind="camera", **over):
    """Post a beat through the real ingest path, then backdate it in place."""
    r = await client.post(
        "/api/v1/telemetry",
        json={"site": "north40", "node": node, "kind": kind, **over},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    row_id = r.json()["id"]
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Telemetry).where(Telemetry.id == row_id).values(received_at=at)
        )
        await session.commit()
    return row_id


def norm(value):
    """SQLite hands datetimes back naive, Postgres aware — compare in UTC."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def flatten(payload):
    """Volatile wall-clock fields out, datetimes normalised, ids kept as-is."""
    out = []
    for row in payload:
        row = dict(row)
        assert row.pop("last_seen_at") is not None  # stamped by ingest, not seeded
        latest = row.get("latest")
        if latest is not None:
            latest = dict(latest)
            latest["received_at"] = norm(latest["received_at"])
            row["latest"] = latest
        out.append(row)
    return out


async def test_nodes_health_payload(client, device_token):
    # --- c1: every derived field, each from a different row -------------------
    await seed(
        client,
        device_token,
        "c1",
        NOW - timedelta(days=30),
        battery_v=3.40,
        extra={"prev_node": OLD_SLUG, "hops": 2},
    )
    # Outside the 6 h window: this health/sd pair must be invisible, and its
    # push_fails=9 must not become the window minimum.
    await seed(
        client,
        device_token,
        "c1",
        NOW - timedelta(hours=7),
        battery_v=3.38,
        extra={"health": {"pir_wakes": 10, "captures": 4, "push_fails": 9}, "sd": "u"},
    )
    # Oldest in-window health row, and it matches the newest — so the minimum
    # has to come from the row in the MIDDLE or push_failing reads False.
    await seed(
        client,
        device_token,
        "c1",
        NOW - timedelta(hours=5),
        battery_v=3.36,
        extra={"health": {"pir_wakes": 20, "captures": 8, "push_fails": 5}, "sd": "n"},
    )
    await seed(
        client,
        device_token,
        "c1",
        NOW - timedelta(hours=4),
        battery_v=3.355,
        extra={"health": {"pir_wakes": 24, "captures": 8, "push_fails": 2}},
    )
    # A negative counter is not a candidate for the minimum.
    await seed(
        client,
        device_token,
        "c1",
        NOW - timedelta(hours=3),
        battery_v=3.35,
        extra={"health": {"push_fails": -1}},
    )
    # Newest windowed row carrying health — and it carries no sd, so the state
    # above still stands.
    await seed(
        client,
        device_token,
        "c1",
        NOW - timedelta(hours=2),
        battery_v=3.34,
        extra={"health": {"pir_wakes": 30, "captures": 9, "push_fails": 5}},
    )
    tie = NOW - timedelta(hours=1)
    await seed(client, device_token, "c1", tie, battery_v=3.33, boot_reason="DSLEEP")
    # Same received_at, higher id: this is the one `latest` must show.
    await seed(
        client,
        device_token,
        "c1",
        tie,
        battery_v=3.32,
        boot_reason="POWERON",
        temp_c=19.5,
        rssi=-91,
        snr=8.5,
        uptime_s=7200,
        fw_version="leaf-0.21.0",
    )

    # --- c5: the recency window is the only thing keeping these two quiet -----
    await seed(
        client,
        device_token,
        "c5",
        NOW - timedelta(hours=8),
        battery_v=3.20,
        extra={"health": {"pir_wakes": 1, "captures": 1, "push_fails": 0}, "sd": "u"},
    )
    await seed(
        client,
        device_token,
        "c5",
        NOW - timedelta(hours=1),
        battery_v=3.19,
        extra={"health": {"pir_wakes": 7, "captures": 3, "push_fails": 2}},
    )

    # --- leaf-old: the row still holding c1's other half ----------------------
    await seed(client, device_token, OLD_SLUG, NOW - timedelta(hours=4), battery_v=3.10)

    # --- c3: junk in every slot -----------------------------------------------
    await seed(
        client,
        device_token,
        "c3",
        NOW - timedelta(hours=1),
        battery_v=3.05,
        extra={"health": "not-a-dict", "sd": 5, "prev_node": 42},
    )
    await seed(client, device_token, "c3", NOW - timedelta(minutes=30), battery_v=3.04)

    # --- c4: a node with no telemetry at all ----------------------------------
    await seed(client, device_token, "c4", NOW - timedelta(hours=1), battery_v=3.01)
    async with get_sessionmaker()() as session:
        cam = (await session.scalars(select(Camera).where(Camera.slug == "c4"))).one()
        await session.execute(delete(Telemetry).where(Telemetry.camera_id == cam.id))
        await session.commit()

    r = await client.get("/api/v1/nodes/health")
    assert r.status_code == 200, r.text
    rows = {row["slug"]: row for row in flatten(r.json())}
    assert set(rows) == {"c1", "c3", "c4", "c5", OLD_SLUG}

    c1 = rows["c1"]
    assert c1["latest"] == {
        "received_at": tie,
        "battery_v": 3.32,
        "temp_c": 19.5,
        "pressure_hpa": None,
        "rssi": -91.0,
        "snr": 8.5,
        "uptime_s": 7200,
        "boot_reason": "POWERON",
        "fw_version": "leaf-0.21.0",
        "extra": None,
    }
    assert c1["health"] == {
        "pir_wakes": 30,
        "captures": 9,
        "push_fails": 5,
        "battery_v": None,
    }
    assert c1["push_failing"] is True  # 5 now vs a windowed minimum of 2
    assert c1["storage_state"] == "no_card"  # "n" from the 5 h row, not "u" from 7 h
    assert c1["prev_node"] == OLD_SLUG  # 30 days old, and not the newest row
    assert c1["prev_node_id"] == rows[OLD_SLUG]["id"]
    assert c1["kind"] == "camera"
    assert c1["site_slug"] == "north40"
    assert c1["last_battery_v"] == 3.32
    assert c1["last_photo_at"] is None

    c5 = rows["c5"]
    # Everything the 8 h row said is out of the window: its sd is not reported,
    # and its push_fails=0 is not the minimum push_failing compares against.
    assert c5["health"] == {"pir_wakes": 7, "captures": 3, "push_fails": 2, "battery_v": None}
    assert c5["push_failing"] is False
    assert c5["storage_state"] is None

    old = rows[OLD_SLUG]
    assert old["latest"]["battery_v"] == 3.10
    assert (old["health"], old["push_failing"], old["storage_state"]) == (None, False, None)
    assert old["prev_node"] is None and old["prev_node_id"] is None

    c3 = rows["c3"]
    assert c3["latest"]["battery_v"] == 3.04
    assert c3["latest"]["received_at"] == NOW - timedelta(minutes=30)
    assert (c3["health"], c3["push_failing"], c3["storage_state"]) == (None, False, None)
    assert c3["prev_node"] is None and c3["prev_node_id"] is None

    assert rows["c4"]["latest"] is None
    assert rows["c4"]["health"] is None


async def test_health_stops_reporting_prev_node_once_merged(client, device_token):
    """The no-window rule must not resurrect a merge that already happened."""
    await seed(client, device_token, OLD_SLUG, NOW - timedelta(days=40), battery_v=3.2)
    await seed(
        client,
        device_token,
        "c9",
        NOW - timedelta(days=30),
        battery_v=3.3,
        extra={"prev_node": OLD_SLUG},
    )
    rows = {r["slug"]: r for r in (await client.get("/api/v1/nodes/health")).json()}
    assert rows["c9"]["prev_node"] == OLD_SLUG

    # Merge leaf-old into c9; c9 now answers to the slug it reported.
    r = await client.post(
        f"/api/v1/cameras/{rows['c9']['id']}/merge", json={"source": OLD_SLUG}
    )
    assert r.status_code == 200, r.text
    rows = {r["slug"]: r for r in (await client.get("/api/v1/nodes/health")).json()}
    assert rows["c9"]["prev_node"] is None
    assert rows["c9"]["prev_node_id"] is None


def test_prev_node_predicate_matches_the_partial_index():
    """The index only works if its predicate is TEXTUALLY the query's.

    Postgres decides whether a partial index applies by comparing expression
    trees, not by reasoning about meaning, so ix_telemetry_prev_node has to
    spell the query's predicate exactly. Measured on the dev database: an index
    written the obvious way, `WHERE extra ? 'prev_node'`, was never used, and
    neither was `WHERE (extra ->> 'prev_node') IS NOT NULL`, because
    `.as_string()` compiles to a `::character varying` relabel around the `->>`.

    Nothing in the SQLite suite can catch that drifting — the index does not
    exist here and the query is right either way — so pin the compiled text.
    If this fails, the index has silently stopped being used: re-run the EXPLAIN
    on a real Postgres and move BOTH sides together.
    """
    import importlib.util
    from pathlib import Path

    from sqlalchemy.dialects import postgresql

    from trailcam.api.telemetry import PREV_NODE_PRESENT

    compiled = str(
        PREV_NODE_PRESENT.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0014_telemetry_prev_node_index.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0014", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert compiled == migration.PREV_NODE_PREDICATE
