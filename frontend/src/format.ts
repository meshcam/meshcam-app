/** Short relative time for grid captions: "just now", "8m ago", "3h ago", "5d ago". */
export function timeAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}mo ago`
  return `${Math.floor(days / 365)}y ago`
}

const easternFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
})

/** Full timestamp in US Eastern time, e.g. "Jul 2, 2026, 6:41 AM EDT". */
export function formatEastern(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return easternFormatter.format(date)
}

/** Humanized uptime for node cards: "3d 4h", "5h 12m", "42m", "30s". */
export function uptimeLabel(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const days = Math.floor(s / 86_400)
  const hours = Math.floor((s % 86_400) / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m`
  return `${s}s`
}

const easternTimeOnly = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  hour: 'numeric',
  minute: '2-digit',
})

const easternDateOnly = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  month: 'short',
  day: 'numeric',
})

/**
 * Chart time label in US Eastern: time-of-day for short spans ("6:41 PM"),
 * date for long ones ("Jun 25"). `withTime` adds the clock time to date labels
 * (used by hover readouts).
 */
export function chartTimeLabel(ms: number, spanHours: number, withTime = false): string {
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return ''
  if (spanHours <= 36) return easternTimeOnly.format(date)
  const day = easternDateOnly.format(date)
  return withTime ? `${day}, ${easternTimeOnly.format(date)}` : day
}

// en-CA gives ISO-shaped YYYY-MM-DD, which we use as a stable day key.
const easternDayKeyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

/** Calendar day (YYYY-MM-DD) a timestamp falls on in US Eastern time. */
export function easternDayKey(iso: string | number): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return easternDayKeyFmt.format(date)
}

const dayLabelFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
})

const dayLabelYearFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

/** Feed day-separator label: "Today", "Yesterday", "Mon, Jun 30", "Sat, Nov 8, 2025". */
export function dayLabel(iso: string): string {
  const key = easternDayKey(iso)
  if (!key) return ''
  const today = easternDayKey(Date.now())
  if (key === today) return 'Today'
  if (key === easternDayKey(Date.now() - 86_400_000)) return 'Yesterday'
  const date = new Date(iso)
  return key.slice(0, 4) === today.slice(0, 4)
    ? dayLabelFmt.format(date)
    : dayLabelYearFmt.format(date)
}

function easternOffsetMs(utcMs: number): number {
  const tz = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    timeZoneName: 'longOffset',
  })
    .formatToParts(utcMs)
    .find((p) => p.type === 'timeZoneName')?.value
  const m = tz?.match(/GMT([+-])(\d{2}):(\d{2})/)
  if (!m) return -5 * 3_600_000
  return (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 3600 + Number(m[3]) * 60) * 1000
}

// Midnight Eastern of a YYYY-MM-DD day, as a UTC instant. Offset sampled at
// that day's noon so the answer is stable across the 2am DST seams.
function easternMidnightUtc(day: string): number {
  const offset = easternOffsetMs(Date.parse(`${day}T12:00:00Z`))
  return Date.parse(`${day}T00:00:00Z`) - offset
}

/**
 * UTC instants bounding a US-Eastern calendar day: [start, end). The end is
 * the next day's midnight, so DST 23/25-hour days come out right.
 */
export function easternDayBounds(day: string): { start: string; end: string } {
  const nextDay = new Date(Date.parse(`${day}T12:00:00Z`) + 86_400_000)
    .toISOString()
    .slice(0, 10)
  return {
    start: new Date(easternMidnightUtc(day)).toISOString(),
    end: new Date(easternMidnightUtc(nextDay)).toISOString(),
  }
}

/** Expiry line for the detail overlay: "Saved forever", "Expires in 23d", … */
export function expiryLabel(expiresAt: string | null, keep: boolean): string {
  if (keep || !expiresAt) return 'Saved forever'
  const remaining = new Date(expiresAt).getTime() - Date.now()
  if (Number.isNaN(remaining)) return 'Saved forever'
  if (remaining <= 0) return 'Expiring soon'
  const days = Math.floor(remaining / 86_400_000)
  if (days >= 1) return `Expires in ${days}d`
  const hours = Math.floor(remaining / 3_600_000)
  if (hours >= 1) return `Expires in ${hours}h`
  const minutes = Math.max(1, Math.floor(remaining / 60_000))
  return `Expires in ${minutes}m`
}
