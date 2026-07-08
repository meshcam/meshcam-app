export interface Me {
  email: string
  name: string
  /** Public read-only demo instance — the UI hides every write control. */
  demo: boolean
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

export type View = 'photos' | 'nodes' | 'settings'

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

export type NodeKind = 'camera' | 'relay' | 'gateway'

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

export type FullRequestStatus = 'pending' | 'delivered' | 'done' | 'failed' | 'expired'

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
  completed_at: string | null
}

export interface FullRequest {
  status: FullRequestStatus
  quality: 'standard' | 'max'
  requested_by: string | null
  created_at: string
  delivered_at: string | null
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
