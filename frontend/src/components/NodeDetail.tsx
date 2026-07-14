import { X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import * as api from '../api'
import { UnauthorizedError } from '../api'
import { timeAgo } from '../format'
import type { NodeHealth, TelemetryPoint } from '../types'
import NodeCommands from './NodeCommands'
import TelemetryChart from './TelemetryChart'
import UplinkPanel from './UplinkPanel'

interface NodeDetailProps {
  node: NodeHealth
  /** Full health list — the uplink panel finds the node's site gateway in it. */
  nodes: NodeHealth[]
  /** Public read-only demo: hides the operator command panel. */
  demo: boolean
  onClose: () => void
  onUnauthorized: () => void
}

const RANGES = [
  { label: '24h', hours: 24 },
  { label: '7d', hours: 168 },
  { label: '30d', hours: 720 },
] as const

// Series hues validated against the app's dark surface (CVD-safe as a set).
const BATTERY_COLOR = '#5fa142'
const RADIO_COLOR = '#3987e5'
const TEMP_COLOR = '#c98500'

// Friendly labels for the gateway's `extra` telemetry keys; unknown keys fall
// back to a title-cased version so any future stat still renders.
const STAT_LABELS: Record<string, string> = {
  ip: 'IP address',
  wifi_ssid: 'WiFi network',
  soc_temp_c: 'SoC temp',
  lora_profile: 'LoRa profile',
  last_mesh_contact_s: 'Last mesh contact',
  lora_rssi: 'Mesh RSSI',
  lora_snr: 'Mesh SNR',
  free_heap: 'Free heap',
  uploads: 'Images forwarded',
  res_ok: 'Mesh transfers OK',
  res_fail: 'Mesh transfers failed',
  announces: 'Announces heard',
  chunks: 'Photo chunks',
  via: 'Heard via',
  status: 'Last event',
}

// `extra` keys that are raw debug payloads, not stats — never render them.
const STAT_SKIP = new Set(['packet'])

function labelFor(key: string): string {
  return STAT_LABELS[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatUptime(s: number): string {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

function formatStat(key: string, value: unknown): string {
  if (value == null) return '—'
  if (key === 'soc_temp_c') return `${value} °C`
  if (key === 'lora_rssi') return `${value} dBm`
  if (key === 'lora_snr') return `${value} dB`
  if (key === 'free_heap') return `${Math.round(Number(value) / 1024)} KB`
  if (key === 'last_mesh_contact_s') return `${value}s ago`
  return String(value)
}

export default function NodeDetail({
  node,
  nodes,
  demo,
  onClose,
  onUnauthorized,
}: NodeDetailProps) {
  const [hours, setHours] = useState<number>(168)
  const [attempt, setAttempt] = useState(0)
  const [points, setPoints] = useState<TelemetryPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getNodeTelemetry(node.id, hours)
      .then((data) => {
        if (!cancelled) setPoints(data.points)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof UnauthorizedError) {
          onUnauthorized()
          return
        }
        setError('Failed to load telemetry')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [node.id, hours, attempt, onUnauthorized])

  // Keyboard: Escape closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Lock body scroll while the overlay is open.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  const closeOnSelf = (e: ReactMouseEvent) => {
    if (e.target === e.currentTarget) onClose()
  }

  const isGateway = node.kind === 'gateway'

  const charts = useMemo(() => {
    const times: number[] = []
    const battery: (number | null)[] = []
    const temp: (number | null)[] = []
    const rssi: (number | null)[] = []
    const snr: (number | null)[] = []
    for (const p of points) {
      const t = new Date(p.received_at).getTime()
      if (Number.isNaN(t)) continue
      times.push(t)
      battery.push(p.battery_v)
      temp.push(p.temp_c)
      rssi.push(p.rssi)
      snr.push(p.snr)
    }
    return { times, battery, temp, rssi, snr }
  }, [points])

  // This node has never reported a battery voltage — either it runs on external
  // power (bench/USB, mains gateway) or its battery sense isn't fitted yet. The
  // leaf firmware's battery ADC read is still a TODO, so the device itself can't
  // tell us which; all we can honestly say is "no battery data".
  const everBattery = node.last_battery_v != null || node.latest?.battery_v != null

  // Current-value stats from the latest snapshot — scalar fields plus every
  // present key in `extra` (the gateway reports ip, wifi_ssid, soc_temp_c,
  // lora_profile, mesh contact/signal, and forwarding counters here).
  const statRows = useMemo<Array<[string, string]>>(() => {
    const rows: Array<[string, string]> = []
    if (node.kind === 'gateway') rows.push(['Power', 'Plugged in (mains)'])
    else if (!everBattery) rows.push(['Power', 'External (no battery reported)'])
    const l = node.latest
    if (!l) return rows
    if (l.fw_version) rows.push(['Firmware', l.fw_version])
    if (l.boot_reason) rows.push(['Boot reason', l.boot_reason])
    if (l.uptime_s != null) rows.push(['Uptime', formatUptime(l.uptime_s)])
    if (l.extra) {
      for (const [k, v] of Object.entries(l.extra)) {
        if (v == null || STAT_SKIP.has(k) || typeof v === 'object') continue
        rows.push([labelFor(k), formatStat(k, v)])
      }
    }
    return rows
  }, [node.kind, node.latest, everBattery])

  return (
    <div
      className="detail-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`Telemetry for ${node.name}`}
      onClick={closeOnSelf}
    >
      <div className="detail-topbar">
        <span className="detail-title">{node.name}</span>
        <button
          type="button"
          className="btn icon-btn detail-close"
          aria-label="Close"
          onClick={onClose}
        >
          <X size={20} aria-hidden="true" />
        </button>
      </div>

      <div className="node-detail-panel" onClick={closeOnSelf}>
        <div className="node-detail-sub">
          {node.kind} · {node.site_slug} · last seen{' '}
          {node.last_seen_at ? timeAgo(node.last_seen_at) : 'never'}
        </div>

        <UplinkPanel node={node} nodes={nodes} />

        {statRows.length > 0 && (
          <dl className="node-stats">
            {statRows.map(([label, value]) => (
              <div className="node-stat" key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}

        <div className="range-row">
          <div className="range-toggle" role="group" aria-label="Time range">
            {RANGES.map((range) => (
              <button
                key={range.hours}
                type="button"
                className={`range-btn${hours === range.hours ? ' active' : ''}`}
                aria-pressed={hours === range.hours}
                onClick={() => setHours(range.hours)}
              >
                {range.label}
              </button>
            ))}
          </div>
          {loading && <span className="spinner" aria-label="Loading telemetry" />}
        </div>

        {error ? (
          <div className="grid-status grid-error" role="alert">
            <span>{error}</span>
            <button
              type="button"
              className="btn"
              onClick={() => setAttempt((n) => n + 1)}
            >
              Retry
            </button>
          </div>
        ) : (
          <>
            {/* No battery data ever → the chart would be blank forever (external
                power, or battery sense not fitted) — the Power stat row carries
                that instead of an empty graph. Same deal for temperature. */}
            {(everBattery || charts.battery.some((v) => v != null)) && (
              <TelemetryChart
                title="Battery (V)"
                times={charts.times}
                spanHours={hours}
                axisDecimals={2}
                series={[
                  {
                    label: 'Battery',
                    color: BATTERY_COLOR,
                    unit: ' V',
                    decimals: 2,
                    values: charts.battery,
                  },
                ]}
              />
            )}
            <TelemetryChart
              title={isGateway ? 'WiFi signal (dBm)' : 'Mesh radio (LoRa 915 MHz)'}
              hint={
                isGateway
                  ? "Strength of the gateway's WiFi uplink to the house network."
                  : "This node's LoRa signal as received by the gateway (RSSI / SNR)."
              }
              times={charts.times}
              spanHours={hours}
              axisDecimals={0}
              series={[
                {
                  label: 'RSSI',
                  color: RADIO_COLOR,
                  unit: ' dBm',
                  decimals: 0,
                  values: charts.rssi,
                },
                ...(isGateway
                  ? []
                  : [
                      {
                        label: 'SNR',
                        color: RADIO_COLOR,
                        unit: ' dB',
                        decimals: 1,
                        faint: true,
                        values: charts.snr,
                      },
                    ]),
              ]}
            />
            {(node.latest?.temp_c != null || charts.temp.some((v) => v != null)) && (
              <TelemetryChart
                title="Temperature (°C)"
                times={charts.times}
                spanHours={hours}
                axisDecimals={1}
                series={[
                  {
                    label: 'Temp',
                    color: TEMP_COLOR,
                    unit: ' °C',
                    decimals: 1,
                    values: charts.temp,
                  },
                ]}
              />
            )}
          </>
        )}

        {!demo && <NodeCommands node={node} onUnauthorized={onUnauthorized} />}
      </div>
    </div>
  )
}
