import {
  BatteryFull,
  BatteryLow,
  BatteryWarning,
  Camera,
  Compass,
  RadioTower,
  Router,
  TriangleAlert,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { timeAgo, uptimeLabel } from '../format'
import { batteryLevel, nodeStatus, STATUS_LABEL } from '../nodes'
import type { NodeHealth } from '../types'

const KIND_ICON: Record<string, LucideIcon> = {
  camera: Camera,
  relay: RadioTower,
  gateway: Router,
  surveyor: Compass,
}

const BATTERY_ICON: Record<string, LucideIcon> = {
  good: BatteryFull,
  low: BatteryLow,
  critical: BatteryWarning,
}

interface NodeCardProps {
  node: NodeHealth
  onClick: () => void
}

export default function NodeCard({ node, onClick }: NodeCardProps) {
  const status = nodeStatus(node.last_seen_at)
  const latest = node.latest
  const batteryV = latest?.battery_v ?? node.last_battery_v
  const KindIcon = KIND_ICON[node.kind]
  const level = batteryV != null ? batteryLevel(batteryV) : null
  const BatteryIcon = level != null ? BATTERY_ICON[level] : null

  const facts: { label: string; value: string }[] = []
  if (latest?.temp_c != null) {
    facts.push({ label: 'Temp', value: `${latest.temp_c.toFixed(1)} °C` })
  }
  if (latest?.rssi != null || latest?.snr != null) {
    const rssi = latest?.rssi != null ? `${latest.rssi} dBm` : ''
    const snr = latest?.snr != null ? `${latest.snr} dB SNR` : ''
    // A gateway's top-level rssi is its WiFi uplink; leaf/relay rssi is the
    // 915 MHz LoRa link as heard by the gateway. Label which radio it is.
    facts.push({
      label: node.kind === 'gateway' ? 'WiFi' : 'Mesh',
      value: [rssi, snr].filter(Boolean).join(' / '),
    })
  }
  if (latest?.uptime_s != null) {
    facts.push({ label: 'Up', value: uptimeLabel(latest.uptime_s) })
  }
  if (node.last_photo_at) {
    facts.push({ label: 'Last photo', value: timeAgo(node.last_photo_at) })
  }
  if (node.health?.captures != null) {
    // Since-boot counters off the announce path (bug 5): wakes / captures / failed pushes
    facts.push({
      label: 'Boot',
      value: `${node.health.pir_wakes ?? '?'} wakes · ${node.health.captures} caps · ${
        node.health.push_fails ?? 0
      } push-fail`,
    })
  }

  const fine = [
    latest?.fw_version ? `fw ${latest.fw_version}` : null,
    latest?.boot_reason ? `boot ${latest.boot_reason}` : null,
  ].filter(Boolean)

  return (
    <button
      type="button"
      className="node-card"
      onClick={onClick}
      aria-label={`${node.name}, ${STATUS_LABEL[status]}`}
    >
      <div className="node-card-head">
        <span className="node-name">{node.name}</span>
        <span className={`kind-badge kind-${node.kind}`}>
          {KindIcon && <KindIcon size={12} aria-hidden="true" />}
          {node.kind}
        </span>
      </div>
      <div className="node-slug">{node.slug}</div>

      <div className="node-status">
        <span className={`status-dot status-${status}`} aria-hidden="true" />
        <span className="node-status-label">{STATUS_LABEL[status]}</span>
        <span className="node-status-time">
          {node.last_seen_at ? timeAgo(node.last_seen_at) : 'never'}
        </span>
        {node.push_failing && (
          <span
            className="push-failing-pill"
            title="Capturing fine but its photo pushes are failing — the mesh link back is broken (this looked like a quiet cam for 21 h on 07-15)"
          >
            <TriangleAlert size={12} aria-hidden="true" />
            pushes failing
          </span>
        )}
      </div>

      {batteryV != null && level != null && (
        <div className="node-battery">
          <span className="node-battery-v">{batteryV.toFixed(2)} V</span>
          <span className={`battery-pill battery-${level}`}>
            {BatteryIcon && <BatteryIcon size={12} aria-hidden="true" />}
            {level}
          </span>
        </div>
      )}

      {facts.length > 0 && (
        <div className="node-facts">
          {facts.map((fact) => (
            <span key={fact.label} className="node-fact">
              <span className="node-fact-label">{fact.label}</span> {fact.value}
            </span>
          ))}
        </div>
      )}

      {fine.length > 0 && <div className="node-fine">{fine.join(' · ')}</div>}
    </button>
  )
}
