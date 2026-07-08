import type { NodeHealth } from './types'

export type NodeStatus = 'dark' | 'quiet' | 'ok'

const HOUR_MS = 3_600_000

/** OK < 24h, Quiet 24-48h, Dark > 48h or never heard from. */
export function nodeStatus(lastSeenAt: string | null): NodeStatus {
  if (!lastSeenAt) return 'dark'
  const age = Date.now() - new Date(lastSeenAt).getTime()
  if (Number.isNaN(age)) return 'dark'
  if (age < 24 * HOUR_MS) return 'ok'
  if (age < 48 * HOUR_MS) return 'quiet'
  return 'dark'
}

export const STATUS_LABEL: Record<NodeStatus, string> = {
  ok: 'OK',
  quiet: 'Quiet',
  dark: 'Dark',
}

/** Sort key: problems float up — Dark, then Quiet, then OK. */
const STATUS_RANK: Record<NodeStatus, number> = { dark: 0, quiet: 1, ok: 2 }

export function statusRank(node: NodeHealth): number {
  return STATUS_RANK[nodeStatus(node.last_seen_at)]
}

export type BatteryLevel = 'good' | 'low' | 'critical'

/** LiFePO4 thresholds: ≥3.25V good, 3.0-3.25 low, <3.0 critical. */
export function batteryLevel(v: number): BatteryLevel {
  if (v >= 3.25) return 'good'
  if (v >= 3.0) return 'low'
  return 'critical'
}
