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
    # Node position for the survey map (the gateway's is the map anchor).
    lat: float | None = None
    lon: float | None = None
    # Retired slugs that still resolve here — the record of a merge.
    # Empty on every node that has never been re-identified, which is most.
    aliases: list[str] = []


class CameraAliasOut(BaseModel):
    slug: str
    created_at: datetime
    note: str | None = None


class CameraMergeIn(BaseModel):
    """Fold a duplicate node row into this one.

    `source` is the row to absorb: a camera UUID ref, or a bare slug in the
    same site (which is what a leaf reports and what the gateway logs, so it
    is the id a human actually has to hand after a re-identification)."""

    source: str
    # Keep answering to the source's slug after the merge. On by default
    # because the usual cause is a board that now announces under a new
    # identity and will keep doing so — without the alias its next heartbeat
    # re-creates the row this call just removed.
    alias: bool = True
    note: str | None = None


class CameraMergeOut(BaseModel):
    camera: CameraOut
    merged_slug: str
    aliased: bool
    # What actually moved, so the caller can see the merge did something.
    photos: int
    telemetry: int
    probes: int
    commands: int
    sightings: int


class CameraPatch(BaseModel):
    name: str | None = None
    notes: str | None = None
    hidden: bool | None = None
    # Set together by the survey map's gateway pin-drop.
    lat: float | None = None
    lon: float | None = None


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
    # burst group (models.Photo.sighting_id) — lets the grouped feed route a
    # live photo event to its tile without a refetch
    sighting_id: uuid.UUID
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


class SightingOut(BaseModel):
    """One burst of same-camera photos — the grouped feed's tile. Under an
    active kept/tag filter, count/kept_count/cover describe the matching
    frames only; started/ended still span them, not the whole burst."""

    id: uuid.UUID  # Photo.sighting_id
    camera_id: uuid.UUID
    camera_name: str
    site_slug: str
    count: int
    kept_count: int
    started_at: datetime  # min(captured_at)
    ended_at: datetime  # max(captured_at)
    last_received_at: datetime  # max(received_at) — the feed sort key
    cover: PhotoOut  # earliest-captured matching frame


class SightingPage(BaseModel):
    items: list[SightingOut]
    next_cursor: str | None


class HistogramBucketOut(BaseModel):
    """One UTC hour of capture activity (only non-empty hours are sent)."""

    hour: datetime
    count: int


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
    # Survey-map basemap default for this deployment (rides /me because it's
    # the one bootstrap call; there is no persisted app-settings store).
    # Empty = privacy-preserving graticule; the browser can override locally.
    map_tile_url: str = ""
    map_tile_attribution: str = ""


class ProbeOut(BaseModel):
    """One surveyor button-press. gw_* is the uplink (what the gateway heard);
    leaf_* is the downlink, measured on the board off the gateway's link
    handshake — the BINDING direction, which runs ~13 dB weaker.

    A leaf_* of null means the surveyor had no reading it could honestly pin to
    that spot (no link, or nothing heard during that probe), never that the
    signal was weak."""

    id: uuid.UUID
    node_id: uuid.UUID  # the surveyor node
    seq: int | None
    kind: str
    received_at: datetime
    lat: float | None
    lon: float | None
    alt: float | None
    hdop: float | None
    sats: int | None
    fix_ok: bool
    profile: str | None
    bytes: int | None
    duration_ms: int | None
    gw_rssi: float | None
    gw_snr: float | None
    leaf_rssi: float | None
    leaf_snr: float | None


class SessionCentroid(BaseModel):
    lat: float
    lon: float


class ProbeSessionOut(BaseModel):
    """A cluster of button presses (gap-based, derived, never stored)."""

    index: int
    started_at: datetime
    ended_at: datetime
    count: int
    fix_count: int  # probes with a trustworthy GPS fix (the plottable ones)
    median_gw_rssi: float | None
    centroid: SessionCentroid | None  # over fix_ok probes; null when none


class TelemetryIn(BaseModel):
    site: str
    node: str
    kind: Literal["camera", "relay", "gateway", "surveyor"] | None = None
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


class NodeHealthCounters(BaseModel):
    """Since-boot leaf health counters, carried in every announce's app_data
    (leaf-0.12.0, bug 5) and forwarded by the gateway as telemetry extra.health."""

    pir_wakes: int | None = None
    captures: int | None = None
    push_fails: int | None = None
    battery_v: float | None = None


# Leaf full-res store state, carried on every announce as "sd=<code>" (leaf-0.17.6)
# and forwarded by the gateway as telemetry extra.sd. The field is ABSENT on a
# healthy card, so absence is not "ok" — it is also what every pre-0.17.6 leaf sends.
# That is why the model below uses None for "not reported" and never invents "ok".
STORAGE_STATE_BY_CODE = {
    "n": "no_card",  # no card answered; the node is on its small internal ring
    "u": "unreadable",  # a card IS present and its filesystem will not mount
    "?": "unknown",  # degraded, but this backend can't separate the two causes
}


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
    health: NodeHealthCounters | None = None
    # True when push_fails climbed within the recent window: the camera is capturing
    # but its pushes are failing — the exact failure mode that looked like a "quiet
    # cam" for 21 h on 07-15 (bug 5).
    push_failing: bool = False
    # Full-res store state as last reported. None = the node never said, which
    # covers both a healthy card and any leaf older than 0.17.6 — do NOT render None as
    # "ok". "unreadable" is the actionable one: a card is in there and the node cannot
    # mount it, which is almost always a >32 GB card still formatted exFAT from the
    # factory, and is what the format_card command exists to fix.
    storage_state: Literal["no_card", "unreadable", "unknown"] | None = None
    # This node came back under a new RNS identity and its history is still split
    #: the slug it says it used to announce under, taken from the newest
    # beat that carried extra.prev_node — not from `latest`, because the leaf tags
    # only a short burst of announces after the change. None once a merge in
    # either direction has reunited the two rows, and on every node that never
    # lost an identity. See identity.py for both rules.
    prev_node: str | None = None
    # The camera row still holding that slug's half of the history, when one
    # answers to it — the merge target. None when nothing does.
    prev_node_id: uuid.UUID | None = None


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
    # "received" = the node acked receipt via its announce (relayed by the gateway,
    # bug 8): stops redelivery, but the command stays live for completion/expiry.
    status: Literal["received", "done", "failed"]
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
    encrypted command packet.

    format_card payload: {"confirm": "erase"} — REQUIRED, and the firmware drops the
    command without it. This is the only command that destroys data. It is safe to
    queue anyway: the leaf refuses to format any card it can actually read, so the
    worst it can do is finish the job on a card already reporting storage_state
    "unreadable"."""

    kind: Literal["maintenance", "sleep", "update_firmware", "format_card"]
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


class ProvisionTokenIn(BaseModel):
    """Machine mint request (/api/v1/provision/device-tokens).

    `name` is the gateway's machine id (`gw-<8hex>`, node-identity.md), not a
    human name — the customer renames the node in the UI afterwards.
    `site_slug` pre-creates the site and the gateway node so the account shows
    the unit the moment it is claimed, instead of staying empty until the first
    beat lands. `replace` is what makes a re-claim work: the same gateway
    claiming again supersedes its own token rather than colliding on the unique
    name.
    """

    name: str
    site_slug: str | None = None
    replace: bool = False


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
    received_at: datetime | None
    completed_at: datetime | None


class FullRequestOut(BaseModel):
    """Diagnostics for a photo's fetch_full command — the over-the-wire story."""

    status: str  # pending | delivered | received | done | failed | expired
    quality: str
    requested_by: str | None
    created_at: datetime
    delivered_at: datetime | None
    received_at: datetime | None
    completed_at: datetime | None
    detail: str | None
    node_last_seen_at: datetime | None
