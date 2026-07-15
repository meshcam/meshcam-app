"""Survey probes: ingest mirroring, fix gating, session clustering, and the
one regression that would be invisible for six months — retention sweeps
deleting survey history."""

import importlib.util
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update

import trailcam.config as configmod
from trailcam.db import get_engine, get_sessionmaker
from trailcam.demosweep import sweep
from trailcam.models import Probe, Telemetry, utcnow
from trailcam.purge import purge_telemetry

# Verified live payload shape (gateway-0.7.x) — see docs/trailcam in homelab.
PROBE_EXTRA = {
    "ms": 22146,
    "bytes": 5309,
    "profile": "sf8/bw125",
    "probe": {
        "seq": 34,
        "kind": "probe",
        "part": 1,
        "parts": 1,
        "lat": 41.564852,
        "lon": -81.072279,
        "alt": 364.7,
        "hdop": 1.18,
        "sats": 8,
        "fix": 1,
        "camera": "surveyor-1",
        "event_id": "prb-34-1",
        "captured_at": 1784055706,
    },
}


async def post_probe(client, token, *, rssi=-68.0, snr=13.2, extra=None, **probe_over):
    extra = extra or {**PROBE_EXTRA, "probe": {**PROBE_EXTRA["probe"], **probe_over}}
    return await client.post(
        "/api/v1/telemetry",
        json={"site": "hemlock-hollow", "node": "surveyor-1", "rssi": rssi, "snr": snr,
              "extra": extra},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_probe_telemetry_creates_probe_row(client, device_token):
    r = await post_probe(client, device_token)
    assert r.status_code == 200, r.text

    probes = (await client.get("/api/v1/probes")).json()
    assert len(probes) == 1
    p = probes[0]
    # Top-level telemetry rssi/snr are the gateway-side (uplink) readings.
    assert p["gw_rssi"] == -68.0
    assert p["gw_snr"] == 13.2
    assert p["seq"] == 34
    assert p["kind"] == "probe"
    assert p["lat"] == 41.564852
    assert p["fix_ok"] is True
    assert p["profile"] == "sf8/bw125"
    assert p["bytes"] == 5309
    assert p["duration_ms"] == 22146
    assert p["leaf_rssi"] is None  # firmware doesn't send the downlink yet

    # The telemetry row is still written too — the live mesh feed reads it.
    recent = (await client.get("/api/v1/mesh/recent")).json()
    assert any(e["extra"] and "probe" in e["extra"] for e in recent)


async def test_plain_telemetry_creates_no_probe(client, device_token):
    r = await client.post(
        "/api/v1/telemetry",
        json={"site": "hemlock-hollow", "node": "c1", "rssi": -70.0,
              "extra": {"status": "checkin"}},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200
    assert (await client.get("/api/v1/probes?fix=all")).json() == []


async def test_malformed_probe_does_not_fail_ingest(client, device_token):
    r = await post_probe(client, device_token, extra={"probe": {"lat": "garbage"}})
    assert r.status_code == 200
    # Row still recorded (defensively parsed), just with nothing trustworthy.
    probes = (await client.get("/api/v1/probes?fix=all")).json()
    assert len(probes) == 1
    assert probes[0]["lat"] is None
    assert probes[0]["fix_ok"] is False


async def test_no_fix_probes_hidden_by_default(client, device_token):
    await post_probe(client, device_token)  # good fix
    # The trap: sats=0/hdop=99.99 with a stale, plausible-looking position.
    await post_probe(client, device_token, seq=35, sats=0, hdop=99.99)
    # The other flavor: never locked, 0,0.
    await post_probe(client, device_token, seq=36, lat=0.0, lon=0.0, sats=0, hdop=99.99)

    assert len((await client.get("/api/v1/probes")).json()) == 1
    got = (await client.get("/api/v1/probes?fix=all")).json()
    assert len(got) == 3
    assert [p["fix_ok"] for p in got] == [True, False, False]


async def test_probe_leaf_side_stored_when_present(client, device_token):
    await post_probe(client, device_token, leaf_rssi=-104, leaf_snr=10.0)
    p = (await client.get("/api/v1/probes")).json()[0]
    assert p["leaf_rssi"] == -104.0
    assert p["leaf_snr"] == 10.0


async def test_sessions_cluster_on_gap(client, device_token):
    for _ in range(3):
        await post_probe(client, device_token)
    now = utcnow()
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(Probe).order_by(Probe.id))).all()
        # Two clusters 45 min apart: [t-50m, t-49m] and [t-5m].
        for row, minutes in zip(rows, (50, 49, 5), strict=True):
            row.received_at = now - timedelta(minutes=minutes)
        await s.commit()

    sessions = (await client.get("/api/v1/probes/sessions")).json()
    assert [s["count"] for s in sessions] == [2, 1]
    assert sessions[0]["fix_count"] == 2
    assert sessions[0]["median_gw_rssi"] == -68.0
    assert sessions[0]["centroid"] is not None

    # gap_min is tunable: with a 60-minute gap they are one session.
    sessions = (await client.get("/api/v1/probes/sessions?gap_min=60")).json()
    assert [s["count"] for s in sessions] == [3]


async def test_probes_filter_by_node_and_window(client, device_token):
    await post_probe(client, device_token)
    cams = (await client.get("/api/v1/cameras")).json()
    surveyor = next(c for c in cams if c["slug"] == "surveyor-1")

    assert len((await client.get(f"/api/v1/probes?node={surveyor['id']}")).json()) == 1
    assert (await client.get(f"/api/v1/probes?node={uuid.uuid4()}")).status_code == 404

    future = (utcnow() + timedelta(days=1)).isoformat()
    r = await client.get("/api/v1/probes", params={"from": future})
    assert r.json() == []


# --- the regression that matters: retention must never eat survey history ---


async def _age_everything(days: int) -> None:
    old = utcnow() - timedelta(days=days)
    async with get_sessionmaker()() as s:
        await s.execute(update(Telemetry).values(received_at=old))
        await s.execute(update(Probe).values(received_at=old))
        await s.commit()


async def _counts() -> tuple[int, int]:
    async with get_sessionmaker()() as s:
        t = await s.scalar(select(func.count()).select_from(Telemetry))
        p = await s.scalar(select(func.count()).select_from(Probe))
        return t, p


async def test_purge_telemetry_never_deletes_probes(client, device_token, monkeypatch):
    await post_probe(client, device_token)
    await post_probe(client, device_token, seq=35)
    await _age_everything(days=3)
    # Even with the most aggressive TTL, probes are untouchable.
    monkeypatch.setattr(configmod.get_settings(), "telemetry_ttl_days", 1)

    deleted = await purge_telemetry()

    assert deleted == 2  # both telemetry carriers aged out…
    t, p = await _counts()
    assert t == 0
    assert p == 2  # …and the survey history is intact


async def test_demosweep_never_deletes_probes(client, device_token, store):
    await post_probe(client, device_token)
    await _age_everything(days=30)  # far past DEMOSWEEP_TELEMETRY_DAYS (7)

    await sweep()

    t, p = await _counts()
    assert t == 0
    assert p == 1


# --- migration 0008 backfill: exact lift + idempotent re-run ---------------


def _load_migration():
    path = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0008_probes.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_backfill_lifts_probes_and_reruns_as_noop(client, device_token):
    # Arrange telemetry the way it exists in the wild: probes in extra,
    # heartbeats without. (Rows land via the API so shapes are authentic.)
    await post_probe(client, device_token)
    await post_probe(client, device_token, seq=35, sats=0, hdop=99.99)
    r = await client.post(
        "/api/v1/telemetry",
        json={"site": "hemlock-hollow", "node": "c1", "rssi": -70.0,
              "extra": {"status": "checkin"}},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200
    # Wipe the ingest-written probes so the backfill starts from the pre-0008
    # world: probes exist only inside telemetry.extra.
    async with get_sessionmaker()() as s:
        for row in (await s.scalars(select(Probe))).all():
            await s.delete(row)
        await s.commit()

    migration = _load_migration()
    async with get_engine().begin() as conn:
        first = await conn.run_sync(migration._backfill)
        again = await conn.run_sync(migration._backfill)
    assert first == 2
    assert again == 0  # re-run is a no-op

    probes = (await client.get("/api/v1/probes?fix=all")).json()
    assert len(probes) == 2
    by_seq = {p["seq"]: p for p in probes}
    assert by_seq[34]["fix_ok"] is True
    assert by_seq[34]["gw_rssi"] == -68.0  # matches telemetry.rssi
    assert by_seq[35]["fix_ok"] is False
