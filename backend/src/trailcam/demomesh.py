"""The demo property + mesh behavior model, shared by the seed script and the
live simulator (trailcam.meshsim).

The property is an *imagined* ~100-acre NE-Ohio parcel — hemlock ravine,
hardwood ridge, beaver pond, old orchard, a foot path down to a lake — the
kind of layout someone staking out a family property for wildlife (plus the
occasional person walking to the water) would wire up: ten cameras, two
relays covering the dead ground, one gateway at the cabin. Names and node
identities are invented; nothing here describes a real property.

Beat shapes (announce packets, rf events, chunked transfers, gateway
heartbeats) mirror what gateway-0.5.x really posts, sampled from trailcam-dev
2026-07. All keys/identities are generated.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

SITE_SLUG = "hemlock-hollow"
SITE_NAME = "Hemlock Hollow"


@dataclass(frozen=True)
class Node:
    slug: str
    name: str
    kind: str  # camera | relay | gateway
    battery_v: float  # nominal LiFePO4 resting voltage for this node
    leaf_id: str  # short id leaf firmware stamps into event_ids
    hops: int  # LoRa hops back to the gateway


# Ten cameras across the habitats you'd actually find on a NE-Ohio hundred:
# mast flats, riparian corridor, wetland edges, conifer bedding, field edges —
# plus the lake path, which sees people as often as deer.
NODES: list[Node] = [
    Node("beech-ridge", "Beech Ridge", "camera", 3.37, "c1", 2),
    Node("creek-crossing", "Creek Crossing", "camera", 3.33, "c2", 1),
    Node("beaver-pond", "Beaver Pond", "camera", 3.35, "c3", 2),
    Node("lake-path", "Lake Path", "camera", 3.38, "c4", 1),
    Node("food-plot", "Food Plot", "camera", 3.36, "c5", 1),
    Node("old-orchard", "Old Orchard", "camera", 3.40, "c6", 1),
    Node("pine-thicket", "Pine Thicket", "camera", 3.19, "c7", 2),  # the low one
    Node("swamp-edge", "Swamp Edge", "camera", 3.31, "c8", 2),
    Node("hayfield-gate", "Hayfield Gate", "camera", 3.42, "c9", 1),
    Node("oak-flats", "Oak Flats", "camera", 3.34, "c10", 2),
    Node("ridgetop-relay", "Ridgetop Relay", "relay", 3.41, "r1", 1),
    Node("pond-knoll-relay", "Pond Knoll Relay", "relay", 3.39, "r2", 1),
    Node("cabin-gateway", "Cabin Gateway", "gateway", 3.44, "gw", 0),
]

CAMERAS = [n for n in NODES if n.kind == "camera"]
BY_SLUG = {n.slug: n for n in NODES}

# --- radio / transfer model (Gate-A measured numbers) ------------------------

CHUNK = 16384  # app-layer chunk ceiling; gateway reassembles
RAW_BPS = {"sf8/bw125": 488, "sf7/bw250": 1367}  # LoRa profile raw bitrates
TRANSFER_PROFILE = "sf7/bw250"  # ADR steps up for bulk transfers
GOODPUT_FRAC = (0.30, 0.42)  # observed goodput vs raw after overhead/turnaround
CHUNK_GAP_S = (0.4, 2.5)  # breather between chunks
WAKE_DELAY_S = (25, 80)  # leaf's next listen window after command delivery
CHUNK_ERROR_PROB = 0.06  # occasional resource failure mid-transfer
CHECKIN_INTERVAL_H = {"camera": 2.0, "relay": 1.0, "gateway": 0.5}


def chunk_plan(size: int, rng: random.Random) -> list[tuple[int, int, float]]:
    """[(chunk_no, nbytes, seconds)] for one full-res pull."""
    n = max(1, math.ceil(size / CHUNK))
    raw = RAW_BPS[TRANSFER_PROFILE]
    plan = []
    for i in range(1, n + 1):
        nbytes = CHUNK if i < n else size - CHUNK * (n - 1)
        bps = raw * rng.uniform(*GOODPUT_FRAC)
        plan.append((i, nbytes, nbytes / bps))
    return plan


# --- beat synthesis -----------------------------------------------------------
# Structurally-valid RNS announce packets so the frontend packet inspector
# decodes them; per-node identities are stable across restarts/reseeds.


def _hexbytes(n: int, r: random.Random) -> str:
    return bytes(r.randint(0, 255) for _ in range(n)).hex()


class _Identity:
    def __init__(self, slug: str):
        r = random.Random(f"meshcam-demo-{slug}")
        self.dest = _hexbytes(16, r)
        self.x25519 = _hexbytes(32, r)
        self.ed25519 = _hexbytes(32, r)
        self.name_hash = _hexbytes(10, r)


_IDENTITIES = {n.slug: _Identity(n.slug) for n in NODES}


def announce_packet(slug: str, at: datetime, app_data: str, rng: random.Random) -> dict:
    """Header + keys + name hash + random hash (w/ emission clock) + signature
    + free-form app data — the layout the packet inspector decodes."""
    ident = _IDENTITIES[slug]
    hops = BY_SLUG[slug].hops
    hex_ = (
        "01"  # flags: HEADER_1, packet type announce
        + f"{hops:02x}"
        + ident.dest
        + "00"  # context
        + ident.x25519
        + ident.ed25519
        + ident.name_hash
        + _hexbytes(5, rng)
        + f"{int(at.timestamp()):010x}"[-10:]  # 5-byte emission clock
        + _hexbytes(64, rng)  # signature
        + app_data.encode().hex()
    )
    return {"hex": hex_, "len": len(hex_) // 2}


def leaf_beat_extra(slug: str, at: datetime, status: str, rng: random.Random) -> dict:
    return {
        "via": "mesh",
        "status": status,
        "packet": announce_packet(slug, at, status, rng),
    }


def rf_grant_extra(rng: random.Random) -> tuple[dict, dict]:
    """ADR stepping up for a transfer: (grant, confirmed)."""
    snr = round(rng.uniform(11.5, 14.0), 1)
    grant = {
        "rf": {
            "idx": 2,
            "snr": snr,
            "event": "grant",
            "profile": TRANSFER_PROFILE,
            "headroom": round(snr + rng.uniform(4.0, 6.0), 1),
        }
    }
    confirmed = {
        "rf": {
            "idx": 2,
            "snr": round(snr + rng.uniform(-0.8, 0.8), 1),
            "event": "confirmed",
            "profile": TRANSFER_PROFILE,
        }
    }
    return grant, confirmed


def chunk_extra(
    event_id: str, quality: str, chunk_no: int, chunks: int, nbytes: int, total: int, seconds: float
) -> dict:
    return {
        "transfer": {
            "ms": round(seconds * 1000),
            "bps": round(nbytes / seconds),
            "bytes": nbytes,
            "chunk": chunk_no,
            "total": total,
            "chunks": chunks,
            "offset": CHUNK * (chunk_no - 1),
            "profile": TRANSFER_PROFILE,
            "quality": quality,
            "raw_bps": RAW_BPS[TRANSFER_PROFILE],
            "event_id": event_id,
        }
    }


def reassembled_extra(event_id: str, quality: str, chunks: int, total: int, wall_s: float) -> dict:
    return {
        "transfer": {
            "ms": round(wall_s * 1000),
            "bps": round(total / wall_s) if wall_s > 0 else None,
            "chunks": chunks,
            "profile": TRANSFER_PROFILE,
            "quality": quality,
            "raw_bps": RAW_BPS[TRANSFER_PROFILE],
            "reassembled": total,
            "event_id": event_id,
        }
    }


def gateway_extra(counters: dict, hour_utc: float, rng: random.Random) -> dict:
    return {
        "ip": "192.168.4.2",
        "wifi_ssid": "cabin-wifi",
        "free_heap": rng.randint(203_000, 216_000),
        "soc_temp_c": round(
            38 + 5 * math.sin((hour_utc - 13) / 24 * 2 * math.pi) + rng.uniform(-1, 1), 1
        ),
        "lora_profile": "sf7/bw250",
        "lora_rssi": rng.randint(-74, -64),
        "lora_snr": round(rng.uniform(11.0, 13.5), 1),
        "last_mesh_contact_s": rng.randint(5, 900),
        **counters,
    }


def diurnal_temp(hour_utc: float) -> float:
    """One shared curve so photo facts agree with the telemetry charts
    (peak 19:00 UTC = 3 PM Eastern)."""
    return 16 + 7 * math.sin((hour_utc - 13) / 24 * 2 * math.pi)


def node_signal(node: Node, rng: random.Random) -> tuple[int, float]:
    """(rssi, snr) for a beat from this node — farther hops read weaker."""
    base = -78 if node.hops <= 1 else -88
    return rng.randint(base - 8, base + 6), round(rng.uniform(4.0, 12.5), 1)


def battery_now(node: Node, at: datetime, rng: random.Random) -> float:
    hour = (at.hour + at.minute / 60) % 24
    solar = 0.05 * math.sin((hour - 6) / 24 * 2 * math.pi)
    return round(node.battery_v + solar + rng.uniform(-0.008, 0.008), 3)


def telemetry_body(node: Node, at: datetime, rng: random.Random, extra: dict) -> dict:
    """A full POST /api/v1/telemetry payload for one beat.

    Gateways mirror what gateway-0.7.x really posts: mains-powered, so no
    battery/BME readings; the top-level rssi is the WiFi uplink (the LoRa side
    rides in `extra` as lora_rssi/lora_snr)."""
    hour = (at.hour + at.minute / 60) % 24
    body = {
        "site": SITE_SLUG,
        "node": node.slug,
        "kind": node.kind,
        "reported_at": (at - timedelta(seconds=rng.randint(1, 8))).isoformat(),
        "battery_v": None,
        "temp_c": None,
        "pressure_hpa": None,
        "boot_reason": "deep_sleep" if node.kind == "camera" else "power_on",
        "fw_version": "gateway-0.7.8" if node.kind == "gateway" else "0.5.0",
        "extra": extra,
    }
    if node.kind == "gateway":
        body["rssi"] = rng.randint(-62, -50)
    else:
        body["battery_v"] = battery_now(node, at, rng)
        body["temp_c"] = round(diurnal_temp(hour) + rng.uniform(-0.8, 0.8), 1)
        body["pressure_hpa"] = round(1014 + 6 * math.sin(at.timestamp() / 216000), 1)
        rssi, snr = node_signal(node, rng)
        body["rssi"], body["snr"] = rssi, snr
    return body
