import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef } from 'react'
import type { TileConfig } from '../survey'
import { formatDbm, formatLeafRssi } from '../survey'
import { formatEastern } from '../format'
import type { Probe } from '../types'

/**
 * The survey map. Leaflet is the one deliberate dependency in this feature —
 * tile loading, panning and Web-Mercator math are not worth hand-rolling —
 * everything drawn on top of it is ours.
 *
 * With no tile config (the privacy default) it renders a plain graticule
 * with a scale bar: relative geometry and distances still work, which is
 * enough for antenna placement, and nothing leaves the browser.
 */

export interface MapMarker {
  probe: Probe
  color: string
  /** Baseline-session context dots: small, faint, not the subject. */
  dim?: boolean
  /** Compare-mode probe with no baseline nearby: neutral hollow ring. */
  hollow?: boolean
  /** Extra popup lines (compare mode adds Δ and match distance). */
  extraHtml?: string
}

interface SurveyMapProps {
  markers: MapMarker[]
  /** Every placed gateway — multi-site installs have more than one anchor. */
  gateways: { lat: number; lon: number; name: string }[]
  tiles: TileConfig | null
  /** Gateway pin-drop mode: next map click reports a position. */
  pinMode: boolean
  onPinDrop: (lat: number, lon: number) => void
  /** Changes when the dataset (not the filter) changes — triggers a re-fit. */
  fitKey: string
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

function probePopupHtml(p: Probe): string {
  const rows: [string, string][] = [
    ['received', formatEastern(p.received_at)],
    ['gw RSSI (uplink)', formatDbm(p.gw_rssi)],
    // Railed instrument — shown raw in the popup, never encoded on the map.
    ['gw SNR (railed)', p.gw_snr == null ? '—' : `${p.gw_snr.toFixed(1)} dB`],
    ['leaf RSSI (downlink)', formatLeafRssi(p.leaf_rssi)],
    ['leaf SNR', p.leaf_snr == null ? '—' : `${p.leaf_snr.toFixed(1)} dB`],
  ]
  if (p.leaf_rssi != null && p.gw_rssi != null && p.leaf_rssi > -104) {
    // Path loss is reciprocal and cancels: asymmetry is a property of the
    // radios, independent of where the operator was standing.
    rows.push(['asymmetry (leaf−gw)', `${(p.leaf_rssi - p.gw_rssi).toFixed(1)} dB`])
  }
  rows.push(
    ['profile', p.profile ?? '—'],
    ['payload', p.bytes == null ? '—' : `${p.bytes} B`],
    ['duration', p.duration_ms == null ? '—' : `${(p.duration_ms / 1000).toFixed(1)} s`],
    ['GPS', `${p.sats ?? '—'} sats · hdop ${p.hdop ?? '—'}`],
    ['alt', p.alt == null ? '—' : `${p.alt.toFixed(0)} m`],
  )
  const title = `Probe${p.seq != null ? ` #${p.seq}` : ''}${p.kind !== 'probe' ? ` (${esc(p.kind)})` : ''}`
  return (
    `<div class="probe-popup"><div class="probe-popup-title">${title}</div>` +
    rows
      .map(([k, v]) => `<div class="probe-popup-row"><span>${k}</span><b>${v}</b></div>`)
      .join('') +
    '</div>'
  )
}

/** Graticule step (degrees) that lands 4–10 lines across the given span. */
function graticuleStep(spanDeg: number): number {
  const steps = [10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001, 0.0005]
  for (const s of steps) {
    if (spanDeg / s >= 4) return s
  }
  return steps[steps.length - 1]
}

function drawGraticule(map: L.Map, group: L.LayerGroup): void {
  group.clearLayers()
  const b = map.getBounds()
  const step = graticuleStep(Math.max(b.getEast() - b.getWest(), b.getNorth() - b.getSouth()))
  const style: L.PolylineOptions = { color: '#888', weight: 0.5, opacity: 0.4, interactive: false }
  for (let lat = Math.floor(b.getSouth() / step) * step; lat <= b.getNorth(); lat += step) {
    L.polyline([[lat, b.getWest()], [lat, b.getEast()]], style).addTo(group)
  }
  for (let lon = Math.floor(b.getWest() / step) * step; lon <= b.getEast(); lon += step) {
    L.polyline([[b.getSouth(), lon], [b.getNorth(), lon]], style).addTo(group)
  }
}

export default function SurveyMap({
  markers,
  gateways,
  tiles,
  pinMode,
  onPinDrop,
  fitKey,
}: SurveyMapProps) {
  const divRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<L.Map | null>(null)
  const markerGroupRef = useRef<L.LayerGroup | null>(null)
  const fittedKeyRef = useRef<string | null>(null)
  // Handlers live in refs so the Leaflet listeners (bound once) stay current.
  const pinModeRef = useRef(pinMode)
  const onPinDropRef = useRef(onPinDrop)
  pinModeRef.current = pinMode
  onPinDropRef.current = onPinDrop

  useEffect(() => {
    if (!divRef.current || mapRef.current) return
    const map = L.map(divRef.current, {
      center: [39.5, -84],
      zoom: 4,
      zoomSnap: 0.5,
    })
    L.control.scale({ metric: true, imperial: true }).addTo(map)
    markerGroupRef.current = L.layerGroup().addTo(map)
    map.on('click', (e: L.LeafletMouseEvent) => {
      if (pinModeRef.current) onPinDropRef.current(e.latlng.lat, e.latlng.lng)
    })
    mapRef.current = map
    return () => {
      mapRef.current = null
      markerGroupRef.current = null
      fittedKeyRef.current = null
      map.remove()
    }
  }, [])

  // Basemap: tiles when configured, hand-rolled graticule otherwise. The
  // cleanup owns whatever this run added, so switching in Settings swaps
  // layers without leaking moveend listeners.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (tiles) {
      const layer = L.tileLayer(tiles.url, { attribution: tiles.attribution, maxZoom: 19 })
      layer.addTo(map)
      return () => {
        if (mapRef.current) map.removeLayer(layer)
      }
    }
    const group = L.layerGroup().addTo(map)
    const redraw = () => drawGraticule(map, group)
    map.on('moveend zoomend', redraw)
    redraw()
    return () => {
      if (mapRef.current) {
        map.off('moveend zoomend', redraw)
        map.removeLayer(group)
      }
    }
  }, [tiles])

  // Markers.
  useEffect(() => {
    const map = mapRef.current
    const group = markerGroupRef.current
    if (!map || !group) return
    group.clearLayers()

    for (const m of markers) {
      const p = m.probe
      if (p.lat == null || p.lon == null) continue
      const marker = L.circleMarker([p.lat, p.lon], {
        radius: m.dim ? 4 : 7, // fixed sizes — size encodes nothing
        color: m.hollow ? '#8a8f98' : m.color,
        weight: m.hollow ? 2 : 1.5,
        fillColor: m.hollow ? 'transparent' : m.color,
        fillOpacity: m.dim ? 0.35 : m.hollow ? 0 : 0.85,
        opacity: m.dim ? 0.45 : 1,
      }).addTo(group)
      marker.bindPopup(probePopupHtml(p) + (m.extraHtml ?? ''), { maxWidth: 280 })
    }

    for (const gw of gateways) {
      L.marker([gw.lat, gw.lon], {
        icon: L.divIcon({
          className: 'gw-marker',
          html: `<div class="gw-marker-pin" title="${esc(gw.name)}">⌂</div>`,
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        }),
      })
        .addTo(group)
        .bindPopup(`<b>${esc(gw.name)}</b><br>gateway — probes near it are relative to it`)
    }

    // Fit once per dataset (fitKey), not per filter click — panning around a
    // selection must not snap the view back. Fit to probes first: with
    // multi-site gateways, zooming out to include a distant site's anchor
    // would dwarf the walk being looked at.
    if (fitKey !== fittedKeyRef.current) {
      const pts: L.LatLngExpression[] = markers
        .filter((m) => m.probe.lat != null && m.probe.lon != null)
        .map((m) => [m.probe.lat as number, m.probe.lon as number])
      if (pts.length === 0) pts.push(...gateways.map((g): L.LatLngExpression => [g.lat, g.lon]))
      if (pts.length > 0) {
        map.fitBounds(L.latLngBounds(pts).pad(0.15), { maxZoom: 17 })
        fittedKeyRef.current = fitKey
      }
    }
  }, [markers, gateways, fitKey])

  return (
    <div
      ref={divRef}
      className={`survey-map${pinMode ? ' pin-mode' : ''}`}
      role="application"
      aria-label="Survey map"
    />
  )
}
