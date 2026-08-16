"""Probe extraction: lift a survey probe out of a telemetry payload.

The gateway posts probes as ordinary telemetry with the probe block in
`extra["probe"]` (no firmware/gateway change was made for the probes table —
that contract must keep working). This module turns that payload into a Probe
row; api/telemetry.py calls it on every ingest, and the 0008 migration carries
its own frozen copy of the same logic for the backfill.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from trailcam.models import Probe

logger = logging.getLogger("trailcam.probes")

# RETRACTED: there is no -104 readout floor. It was inferred from two probes on
# the 2026-07-14 walk that both reported exactly -104; the 07-17 and 08-07 board
# dumps run down to -130 and -132 dBm, so -104 was a coincidence in a 20-row
# sample, not a rail. The constant that lived here (and the UI's "≤ -104"
# rendering of it) would have flattened the entire useful range of the downlink
# data the firmware now actually sends. Do not reintroduce a floor without a
# datasheet or a bench sweep behind it.


def gps_fix_ok(
    lat: float | None, lon: float | None, sats: int | None, hdop: float | None
) -> bool:
    """Whether the probe's coordinates are a measurement rather than fiction.

    No-fix probes report either 0,0 (never locked) or a stale last-known
    position with sats=0/hdop=99.99 that looks completely plausible — a probe
    taken at the desk claims "13 m away" with total confidence. The board's
    own `fix` flag is not trusted; this is computed from the quality numbers.
    """
    if lat is None or lon is None or sats is None or hdop is None:
        return False
    return sats >= 4 and hdop < 3.0 and not (lat == 0 and lon == 0)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def probe_from_extra(
    camera_id: int,
    extra: dict,
    gw_rssi: float | None,
    gw_snr: float | None,
    received_at: datetime | None = None,
) -> Probe | None:
    """Build a Probe row from a telemetry payload, or None when there isn't
    a well-formed probe block. Defensive on purpose: a malformed probe must
    never fail the heartbeat ingest it rides along with."""
    p = extra.get("probe")
    if not isinstance(p, dict):
        return None
    try:
        lat, lon = _num(p.get("lat")), _num(p.get("lon"))
        sats, hdop = _int(p.get("sats")), _num(p.get("hdop"))
        captured_ts = _num(p.get("captured_at"))
        return Probe(
            camera_id=camera_id,
            seq=_int(p.get("seq")),
            kind=str(p.get("kind") or "probe")[:16],
            **({"received_at": received_at} if received_at is not None else {}),
            # An unset board RTC sends seconds-since-boot (80, 289, …), which
            # lands in 1970 — stored faithfully, never used for the timeline.
            captured_at=(
                datetime.fromtimestamp(captured_ts, UTC)
                if captured_ts is not None and captured_ts > 0
                else None
            ),
            lat=lat,
            lon=lon,
            alt=_num(p.get("alt")),
            hdop=hdop,
            sats=sats,
            fix_ok=gps_fix_ok(lat, lon, sats, hdop),
            profile=(str(extra["profile"])[:32] if extra.get("profile") else None),
            bytes=_int(extra.get("bytes")),
            duration_ms=_int(extra.get("ms")),
            gw_rssi=gw_rssi,
            gw_snr=gw_snr,
            leaf_rssi=_num(p.get("leaf_rssi")),
            leaf_snr=_num(p.get("leaf_snr")),
        )
    except (ValueError, OSError, OverflowError):
        logger.warning("unparseable probe payload from camera %d: %r", camera_id, p)
        return None
