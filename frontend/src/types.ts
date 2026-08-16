export interface Me {
  email: string
  name: string
  /** Public read-only demo instance — the UI hides every write control. */
  demo: boolean
  /** Deployment-default survey basemap ("" = private graticule); the browser
   *  can override locally (see survey.ts). */
  map_tile_url: string
  map_tile_attribution: string
}

export interface Site {
  id: string
  slug: string
  name: string
  hidden: boolean
}

export interface Camera {
  id: string
  slug: string
  name: string
  kind: NodeKind
  site_slug: string
  notes: string | null
  hidden: boolean
  last_seen_at: string | null
  last_battery_v: number | null
  /** Node position for the survey map (the gateway's is the map anchor). */
  lat: number | null
  lon: number | null
}

export interface PhotoMeta {
  battery_v?: number
  rssi?: number
  snr?: number
  temp_c?: number
  pressure_hpa?: number
}

export interface Tag {
  slug: string
  name: string
}

export interface TagCount extends Tag {
  count: number
}

export interface Photo {
  id: string
  /** Capture event id — matches live mesh transfer beats for progress display. */
  event_id: string
  /** Burst group — routes live photo events to their sighting tile. */
  sighting_id: string
  camera_id: string
  camera_name: string
  site_slug: string
  captured_at: string
  received_at: string
  keep: boolean
  expires_at: string | null
  has_full: boolean
  full_requested: boolean
  thumb_size: number | null
  full_size: number | null
  meta: PhotoMeta
  tags: Tag[]
}

export interface PhotosPage {
  items: Photo[]
  next_cursor: string | null
}

/** One burst of same-camera photos — the grouped feed's tile. Under an active
 *  kept/tag filter, count/kept_count/cover describe the matching frames only. */
export interface Sighting {
  id: string
  camera_id: string
  camera_name: string
  site_slug: string
  count: number
  kept_count: number
  started_at: string
  ended_at: string
  /** max(received_at) — the feed sort key (arrival order, like the flat feed). */
  last_received_at: string
  /** Earliest-captured matching frame (the animal entering). */
  cover: Photo
}

export interface SightingsPage {
  items: Sighting[]
  next_cursor: string | null
}

/** One UTC hour of capture activity (only non-empty hours are sent). */
export interface HistogramBucket {
  hour: string
  count: number
}

export type View = 'photos' | 'nodes' | 'survey' | 'settings'

/** One surveyor button-press. gw_* is the uplink (what the gateway heard —
 *  the one clean instrument); leaf_* is the downlink the board heard, the
 *  binding direction. Null leaf_* = the board had no reading it could pin to
 *  that spot, NOT a weak signal. No readout floor — dumps run to -132 dBm. */
export interface Probe {
  id: string
  node_id: string
  seq: number | null
  kind: string
  received_at: string
  lat: number | null
  lon: number | null
  alt: number | null
  hdop: number | null
  sats: number | null
  /** Whether the coordinates are a measurement rather than fiction — the map
   *  never plots false (the no-fix list carries those). */
  fix_ok: boolean
  profile: string | null
  bytes: number | null
  duration_ms: number | null
  gw_rssi: number | null
  gw_snr: number | null
  leaf_rssi: number | null
  leaf_snr: number | null
}

/** A cluster of button presses (server-derived from received_at gaps). */
export interface ProbeSession {
  index: number
  started_at: string
  ended_at: string
  count: number
  fix_count: number
  median_gw_rssi: number | null
  centroid: { lat: number; lon: number } | null
}

export interface DeviceToken {
  id: string
  name: string
  created_at: string
  last_used_at: string | null
}

export interface DeviceTokenCreated extends DeviceToken {
  /** Plaintext token — shown once at creation, never retrievable again. */
  token: string
}

export interface RetentionStats {
  ttl_days: number
  photo_count: number
  kept_count: number
  expiring_soon: number
  thumb_bytes: number
  full_bytes: number
}

export type NodeKind = 'camera' | 'relay' | 'gateway' | 'surveyor'

export interface NodeLatest {
  received_at: string
  battery_v: number | null
  temp_c: number | null
  pressure_hpa: number | null
  rssi: number | null
  snr: number | null
  uptime_s: number | null
  boot_reason: string | null
  fw_version: string | null
  extra: Record<string, unknown> | null
}

/** Since-boot leaf health counters from every announce (leaf-0.12.0, bug 5). */
export interface NodeHealthCounters {
  pir_wakes: number | null
  captures: number | null
  push_fails: number | null
  battery_v: number | null
}

export interface NodeHealth {
  id: string
  slug: string
  name: string
  kind: NodeKind
  site_slug: string
  last_seen_at: string | null
  last_battery_v: number | null
  last_photo_at: string | null
  latest: NodeLatest | null
  health: NodeHealthCounters | null
  /** push_fails climbed recently: capturing fine, but pushes are failing (bug 5). */
  push_failing: boolean
}

export interface TelemetryPoint {
  received_at: string
  battery_v: number | null
  temp_c: number | null
  pressure_hpa: number | null
  rssi: number | null
  snr: number | null
}

export interface TelemetrySeries {
  points: TelemetryPoint[]
}

export type FullRequestStatus =
  | 'pending'
  | 'delivered'
  | 'received' // node acked receipt via its announce (bug 8) — in flight, not redelivered
  | 'done'
  | 'failed'
  | 'expired'

export type NodeCommandKind = 'maintenance' | 'sleep' | 'update_firmware'

export interface NodeCommand {
  id: number
  kind: string
  status: FullRequestStatus
  payload: Record<string, unknown> | null
  event_id: string | null
  requested_by: string | null
  detail: string | null
  created_at: string
  delivered_at: string | null
  received_at: string | null
  completed_at: string | null
}

export interface FullRequest {
  status: FullRequestStatus
  quality: 'standard' | 'max'
  requested_by: string | null
  created_at: string
  delivered_at: string | null
  received_at: string | null
  completed_at: string | null
  detail: string | null
  node_last_seen_at: string | null
}

export interface MeshTransfer {
  event_id: string
  chunk?: number
  chunks?: number
  reassembled?: number
  // Bandwidth diagnostics (gateway-0.3.1+): per-chunk airtime + goodput, where the
  // chunk sits in the file, and the radio profile the numbers were measured against.
  bytes?: number
  ms?: number
  bps?: number
  offset?: number
  total?: number
  quality?: string
  profile?: string
  // gateway-0.4.0+: the profile's raw bitrate (B/s), so goodput-% is computed against
  // whatever ADR profile the transfer actually ran on, not a hardcoded base rate.
  raw_bps?: number
}

// ADR (adaptive radio profile) lifecycle event from the gateway: it grants profile
// switches from announce SNR, confirms/reverts them, and scans when the leaf goes quiet.
export interface MeshRfEvent {
  event: 'grant' | 'confirmed' | 'revert' | 'adopted' | string
  profile: string
  idx: number
  snr?: number
  headroom?: number
}

export interface MeshEvent {
  at: string
  site: string
  node: string
  kind: string
  rssi: number | null
  snr: number | null
  battery_v: number | null
  fw_version: string | null
  extra: {
    status?: string
    via?: string
    link?: string
    transfer?: MeshTransfer
    transfer_error?: string
    free_heap?: number
    packet?: { len: number; hex: string }
    rf?: MeshRfEvent
  } | null
}
