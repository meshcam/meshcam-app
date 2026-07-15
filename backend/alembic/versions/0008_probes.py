"""survey probes: own table (permanent — outside the telemetry purge) + backfill,
and node lat/lon on cameras for the survey map's gateway anchor.

Probes used to live only as telemetry rows with the payload in extra["probe"],
which put every survey measurement ever taken on purge_telemetry()'s 180-day
deletion clock. This lifts the existing probes out of telemetry.extra into the
new table; the telemetry rows stay (the live mesh feed reads them) and keep
aging out, which is now fine.

The backfill logic is frozen here on purpose (a migration must not drift with
app code); trailcam/probes.py is the living copy used by ingest. Backfill is
idempotent: rows already present (same camera_id + received_at) are skipped,
so re-running it is a no-op.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-14

"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _probe_values(row) -> dict | None:
    """telemetry row -> probes insert values (frozen copy of trailcam.probes)."""
    extra = row.extra
    if not isinstance(extra, dict):
        return None
    p = extra.get("probe")
    if not isinstance(p, dict):
        return None
    lat, lon = _num(p.get("lat")), _num(p.get("lon"))
    sats, hdop = _int(p.get("sats")), _num(p.get("hdop"))
    captured_ts = _num(p.get("captured_at"))
    try:
        captured_at = (
            datetime.fromtimestamp(captured_ts, UTC)
            if captured_ts is not None and captured_ts > 0
            else None
        )
    except (ValueError, OSError, OverflowError):
        captured_at = None
    return {
        "public_id": uuid.uuid4(),
        "camera_id": row.camera_id,
        "seq": _int(p.get("seq")),
        "kind": str(p.get("kind") or "probe")[:16],
        "received_at": row.received_at,
        "captured_at": captured_at,
        "lat": lat,
        "lon": lon,
        "alt": _num(p.get("alt")),
        "hdop": hdop,
        "sats": sats,
        # sats >= 4 AND hdop < 3.0 AND not the 0,0 no-fix sentinel — no-fix
        # probes also report stale-but-plausible coordinates (sats=0,
        # hdop=99.99), which is why the board's own `fix` flag is ignored.
        "fix_ok": (
            lat is not None
            and lon is not None
            and sats is not None
            and hdop is not None
            and sats >= 4
            and hdop < 3.0
            and not (lat == 0 and lon == 0)
        ),
        "profile": (str(extra["profile"])[:32] if extra.get("profile") else None),
        "bytes": _int(extra.get("bytes")),
        "duration_ms": _int(extra.get("ms")),
        # Top-level telemetry rssi/snr on a probe row are the gateway-side
        # (uplink) readings of that probe.
        "gw_rssi": row.rssi,
        "gw_snr": row.snr,
        "leaf_rssi": _num(p.get("leaf_rssi")),
        "leaf_snr": _num(p.get("leaf_snr")),
    }


def _backfill(conn) -> int:
    """Lift probes out of telemetry.extra. Safe to run repeatedly."""
    telemetry = sa.table(
        "telemetry",
        sa.column("id", sa.Integer()),
        sa.column("camera_id", sa.Integer()),
        sa.column("received_at", sa.DateTime(timezone=True)),
        sa.column("rssi", sa.Float()),
        sa.column("snr", sa.Float()),
        sa.column("extra", sa.JSON()),
    )
    probes = sa.table(
        "probes",
        sa.column("public_id", sa.Uuid()),
        sa.column("camera_id", sa.Integer()),
        sa.column("seq", sa.Integer()),
        sa.column("kind", sa.String(16)),
        sa.column("received_at", sa.DateTime(timezone=True)),
        sa.column("captured_at", sa.DateTime(timezone=True)),
        sa.column("lat", sa.Float()),
        sa.column("lon", sa.Float()),
        sa.column("alt", sa.Float()),
        sa.column("hdop", sa.Float()),
        sa.column("sats", sa.Integer()),
        sa.column("fix_ok", sa.Boolean()),
        sa.column("profile", sa.String(32)),
        sa.column("bytes", sa.Integer()),
        sa.column("duration_ms", sa.Integer()),
        sa.column("gw_rssi", sa.Float()),
        sa.column("gw_snr", sa.Float()),
        sa.column("leaf_rssi", sa.Float()),
        sa.column("leaf_snr", sa.Float()),
    )
    # Idempotency key: a probe is one gateway post, so (camera, received_at)
    # identifies it. Datetimes are normalized to UTC before comparing —
    # SQLite hands back naive datetimes, Postgres aware ones.
    def _key(camera_id, received_at):
        ts = received_at
        if isinstance(ts, datetime):
            ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        return (camera_id, ts)

    existing = {
        _key(r.camera_id, r.received_at)
        for r in conn.execute(sa.select(probes.c.camera_id, probes.c.received_at))
    }
    # Python-side filter instead of Postgres's `extra ? 'probe'` so the same
    # migration runs on the sqlite dev flow. Telemetry is at most a few
    # hundred thousand small rows; a one-time scan is fine.
    inserted = 0
    for row in conn.execute(
        sa.select(telemetry).where(telemetry.c.extra.is_not(None))
    ):
        values = _probe_values(row)
        if values is None or _key(values["camera_id"], values["received_at"]) in existing:
            continue
        conn.execute(probes.insert().values(**values))
        existing.add(_key(values["camera_id"], values["received_at"]))
        inserted += 1
    return inserted


def upgrade() -> None:
    op.create_table(
        "probes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=False
        ),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("alt", sa.Float(), nullable=True),
        sa.Column("hdop", sa.Float(), nullable=True),
        sa.Column("sats", sa.Integer(), nullable=True),
        sa.Column("fix_ok", sa.Boolean(), nullable=False),
        sa.Column("profile", sa.String(32), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("gw_rssi", sa.Float(), nullable=True),
        sa.Column("gw_snr", sa.Float(), nullable=True),
        sa.Column("leaf_rssi", sa.Float(), nullable=True),
        sa.Column("leaf_snr", sa.Float(), nullable=True),
    )
    op.create_index("ix_probes_camera_received", "probes", ["camera_id", "received_at"])
    op.create_index("ix_probes_received_at", "probes", ["received_at"])

    op.add_column("cameras", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("cameras", sa.Column("lon", sa.Float(), nullable=True))

    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_column("cameras", "lon")
    op.drop_column("cameras", "lat")
    op.drop_index("ix_probes_received_at", table_name="probes")
    op.drop_index("ix_probes_camera_received", table_name="probes")
    op.drop_table("probes")
