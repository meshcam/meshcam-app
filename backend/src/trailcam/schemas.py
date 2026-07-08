import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SiteOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    hidden: bool = False


class SitePatch(BaseModel):
    name: str | None = None
    hidden: bool | None = None


class CameraOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    kind: str
    site_slug: str
    notes: str | None = None
    hidden: bool = False
    last_seen_at: datetime | None
    last_battery_v: float | None


class CameraPatch(BaseModel):
    name: str | None = None
    notes: str | None = None
    hidden: bool | None = None


class TagOut(BaseModel):
    slug: str
    name: str


class TagCountOut(TagOut):
    count: int


class TagIn(BaseModel):
    name: str


class PhotoOut(BaseModel):
    id: uuid.UUID
    # capture event id — lets the UI correlate live mesh transfer beats
    # (extra.transfer.event_id) with an open photo for progress display
    event_id: str
    camera_id: uuid.UUID
    camera_name: str
    site_slug: str
    captured_at: datetime
    received_at: datetime
    keep: bool
    expires_at: datetime | None
    has_full: bool
    full_requested: bool = False
    thumb_size: int | None
    full_size: int | None
    meta: dict | None
    tags: list[TagOut] = []


class PhotoPage(BaseModel):
    items: list[PhotoOut]
    next_cursor: str | None


class KeepIn(BaseModel):
    keep: bool


class IngestOut(BaseModel):
    id: uuid.UUID
    event_id: str
    stored: str  # "thumb" | "full"


class MeOut(BaseModel):
    email: str
    name: str
    # True on the public demo instance — the frontend hides write controls.
    demo: bool = False


class TelemetryIn(BaseModel):
    site: str
    node: str
    kind: Literal["camera", "relay", "gateway"] | None = None
    reported_at: datetime | None = None
    battery_v: float | None = None
    temp_c: float | None = None
    pressure_hpa: float | None = None
    rssi: float | None = None
    snr: float | None = None
    uptime_s: int | None = None
    boot_reason: str | None = None
    fw_version: str | None = None
    extra: dict | None = None


class TelemetryAck(BaseModel):
    id: int
    node_id: int


class TelemetrySnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    received_at: datetime
    battery_v: float | None
    temp_c: float | None
    pressure_hpa: float | None
    rssi: float | None
    snr: float | None
    uptime_s: int | None
    boot_reason: str | None
    fw_version: str | None
    extra: dict | None = None


class MeshEvent(BaseModel):
    """One mesh-traffic event for the live feed — a telemetry beat, flattened."""

    at: datetime
    site: str
    node: str
    kind: str
    rssi: float | None = None
    snr: float | None = None
    battery_v: float | None = None
    fw_version: str | None = None
    extra: dict | None = None


class NodeHealth(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    kind: str
    site_slug: str
    last_seen_at: datetime | None
    last_battery_v: float | None
    last_photo_at: datetime | None
    latest: TelemetrySnapshot | None


class TelemetryPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    received_at: datetime
    battery_v: float | None
    temp_c: float | None
    pressure_hpa: float | None
    rssi: float | None
    snr: float | None


class TelemetrySeries(BaseModel):
    points: list[TelemetryPoint]


class CommandOut(BaseModel):
    id: int
    kind: str
    site: str
    node: str
    event_id: str | None
    payload: dict | None
    status: str
    created_at: datetime


class CommandAck(BaseModel):
    status: Literal["done", "failed"]
    detail: str | None = None


class RequestFullIn(BaseModel):
    # "max" = sensor-original (large; minutes of LoRa airtime in the field —
    # the UI gates it behind a disclaimer). "standard" = mesh-affordable size.
    quality: Literal["standard", "max"] = "standard"


class NodeCommandIn(BaseModel):
    """Node-level operator command (photo fetches go via /photos/.../request-full).

    maintenance payload: {"ssid": ..., "psk": ..., "minutes": 15} — all optional;
    without WiFi creds the node just stays awake. update_firmware payload:
    {"ssid", "psk", "url", "sha256"} — the node streams the image into its idle
    OTA slot and verifies the sha before committing. Note the payload (psk
    included) is stored in the commands table and travels the mesh inside the
    encrypted command packet."""

    kind: Literal["maintenance", "sleep", "update_firmware"]
    payload: dict | None = None


class DeviceTokenOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


class DeviceTokenCreateIn(BaseModel):
    name: str


class DeviceTokenCreatedOut(DeviceTokenOut):
    """POST response — the only time the plaintext token ever leaves the server."""

    token: str


class RetentionStats(BaseModel):
    ttl_days: int
    photo_count: int
    kept_count: int
    expiring_soon: int  # unstarred photos whose expires_at is within 7 days
    thumb_bytes: int
    full_bytes: int


class NodeCommandOut(BaseModel):
    """One row of a node's command history (operator downlinks + photo fetches)."""

    id: int
    kind: str
    status: str
    payload: dict | None
    event_id: str | None
    requested_by: str | None
    detail: str | None
    created_at: datetime
    delivered_at: datetime | None
    completed_at: datetime | None


class FullRequestOut(BaseModel):
    """Diagnostics for a photo's fetch_full command — the over-the-wire story."""

    status: str  # pending | delivered | done | failed | expired
    quality: str
    requested_by: str | None
    created_at: datetime
    delivered_at: datetime | None
    completed_at: datetime | None
    detail: str | None
    node_last_seen_at: datetime | None
