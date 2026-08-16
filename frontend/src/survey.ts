/**
 * Survey-map domain logic: the measurement rules from the probe analysis,
 * kept out of the components so they stay about rendering.
 *
 * The rules this module encodes (each one is a real trap in the field data):
 * - Colour and rank by gw_rssi only — the one clean instrument. Gateway SNR
 *   is railed (~12.2–14.5 dB at every distance) and carries no information.
 * - leaf_rssi has NO readout floor. A -104 floor used to be encoded here on
 *   the strength of two identical readings in one 20-row walk; the board dumps
 *   since run to -132 dBm. Render it as an ordinary dBm value.
 * - A probe with fix_ok=false has fictional coordinates; it must never be
 *   plotted, but also never silently dropped (count badge + side list).
 */

import type { Probe } from './types'

// --- gw_rssi colour scale (sequential; legend labels in dBm, not adjectives) --

export interface RssiBand {
  /** Inclusive lower bound in dBm (-Infinity for the last band). */
  min: number
  label: string
  color: string
}

export const RSSI_BANDS: RssiBand[] = [
  { min: -50, label: '≥ −50', color: '#1a7f37' },
  { min: -65, label: '−50…−65', color: '#4da53c' },
  { min: -75, label: '−65…−75', color: '#a4c639' },
  { min: -85, label: '−75…−85', color: '#e0a800' },
  { min: -95, label: '−85…−95', color: '#e06d10' },
  { min: -Infinity, label: '< −95', color: '#c62828' },
]

export function rssiColor(rssi: number | null): string {
  if (rssi == null) return '#8a8f98'
  for (const band of RSSI_BANDS) {
    if (rssi >= band.min) return band.color
  }
  return RSSI_BANDS[RSSI_BANDS.length - 1].color
}

// --- compare-mode Δ scale (diverging: red = worse, grey = unchanged) ---------

export interface DeltaBand {
  min: number
  label: string
  color: string
}

export const DELTA_BANDS: DeltaBand[] = [
  { min: 6, label: '≥ +6 dB', color: '#1a7f37' },
  { min: 2, label: '+2…+6', color: '#66bb6a' },
  { min: -2, label: '±2', color: '#8a8f98' },
  { min: -6, label: '−2…−6', color: '#ef6c00' },
  { min: -Infinity, label: '≤ −6 dB', color: '#c62828' },
]

export function deltaColor(delta: number): string {
  for (const band of DELTA_BANDS) {
    if (delta >= band.min) return band.color
  }
  return DELTA_BANDS[DELTA_BANDS.length - 1].color
}

// --- readout formatting -------------------------------------------------------

export function formatDbm(v: number | null): string {
  return v == null ? '—' : `${v.toFixed(v % 1 === 0 ? 0 : 1)} dBm`
}

// --- geometry -----------------------------------------------------------------

/** Great-circle distance in meters (probe-to-probe matching, popup readouts). */
export function haversineM(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6_371_000
  const toRad = Math.PI / 180
  const dLat = (lat2 - lat1) * toRad
  const dLon = (lon2 - lon1) * toRad
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(a))
}

// --- compare mode ---------------------------------------------------------------

export const DEFAULT_MATCH_RADIUS_M = 25
/** Below this many matched pairs a median Δ is refused — this project has
 *  been burned calling a trend off single samples. */
export const MIN_PAIRS_FOR_MEDIAN = 5

export interface MatchedProbe {
  probe: Probe
  /** Nearest baseline probe within the match radius, or null (no baseline
   *  walked here — rendered neutrally, counted, never hidden). */
  baseline: Probe | null
  deltaDb: number | null
  distanceM: number | null
}

export interface CompareResult {
  matched: MatchedProbe[]
  pairCount: number
  unmatchedCount: number
  /** null when pairCount < MIN_PAIRS_FOR_MEDIAN — show the reason instead. */
  medianDeltaDb: number | null
}

/**
 * Position-match current probes against a baseline session. Positions never
 * line up exactly between walks, so each current probe takes the nearest
 * baseline probe within `radiusM`. Both inputs must already be fix_ok.
 */
export function compareSessions(
  current: Probe[],
  baseline: Probe[],
  radiusM: number,
): CompareResult {
  const matched: MatchedProbe[] = current.map((probe) => {
    let best: Probe | null = null
    let bestD = Infinity
    if (probe.lat != null && probe.lon != null) {
      for (const b of baseline) {
        if (b.lat == null || b.lon == null) continue
        const d = haversineM(probe.lat, probe.lon, b.lat, b.lon)
        if (d < bestD) {
          bestD = d
          best = b
        }
      }
    }
    if (best === null || bestD > radiusM || probe.gw_rssi == null || best.gw_rssi == null) {
      return { probe, baseline: null, deltaDb: null, distanceM: null }
    }
    return {
      probe,
      baseline: best,
      deltaDb: probe.gw_rssi - best.gw_rssi,
      distanceM: bestD,
    }
  })
  const deltas = matched
    .map((m) => m.deltaDb)
    .filter((d): d is number => d !== null)
    .sort((a, b) => a - b)
  const mid = deltas.length >> 1
  return {
    matched,
    pairCount: deltas.length,
    unmatchedCount: matched.length - deltas.length,
    medianDeltaDb:
      deltas.length >= MIN_PAIRS_FOR_MEDIAN
        ? deltas.length % 2
          ? deltas[mid]
          : (deltas[mid - 1] + deltas[mid]) / 2
        : null,
  }
}

// --- basemap tiles (privacy) ---------------------------------------------------
//
// Probe coordinates are precise wildlife-camera locations. The deployment
// default arrives via /me (empty = graticule); this browser can override it
// either way in Settings. The override lives in localStorage on purpose:
// opting into a third-party tile host is the operator's per-device choice,
// and it works even on the read-only demo.

const TILE_OVERRIDE_KEY = 'tc-map-tiles'

export const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
export const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

export type TileOverride = 'default' | 'osm' | 'off'

export function getTileOverride(): TileOverride {
  try {
    const v = localStorage.getItem(TILE_OVERRIDE_KEY)
    return v === 'osm' || v === 'off' ? v : 'default'
  } catch {
    return 'default'
  }
}

export function setTileOverride(value: TileOverride): void {
  try {
    if (value === 'default') localStorage.removeItem(TILE_OVERRIDE_KEY)
    else localStorage.setItem(TILE_OVERRIDE_KEY, value)
  } catch {
    // storage unavailable (private mode) — the deployment default applies
  }
}

export interface TileConfig {
  url: string
  attribution: string
}

/** Resolve the basemap for this browser: local override, else deployment default. */
export function resolveTiles(
  deploymentUrl: string,
  deploymentAttribution: string,
): TileConfig | null {
  const override = getTileOverride()
  if (override === 'off') return null
  if (override === 'osm') return { url: OSM_TILE_URL, attribution: OSM_ATTRIBUTION }
  if (!deploymentUrl) return null
  return {
    url: deploymentUrl,
    attribution:
      deploymentUrl === OSM_TILE_URL && !deploymentAttribution
        ? OSM_ATTRIBUTION
        : deploymentAttribution,
  }
}
