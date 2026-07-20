import { useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { chartTimeLabel, easternDayBounds, easternDayKey } from '../format'
import type { HistogramBucket } from '../types'

/**
 * The right-edge timeline rail: the whole library's time range mapped onto a
 * vertical track (top = now, bottom = the first photo). A marker follows the
 * scroll position's day; dragging (or tapping) the rail jumps the feed to that
 * instant via the ?at= anchor — the Immich scroll experience built on time
 * cursors instead of height-mapped virtualization.
 *
 * The mapping is content-proportional (cumulative photo counts from the same
 * histogram the brush uses), so a busy October stretches and a dead January
 * collapses — the rail is the activity distribution, not a wall calendar.
 */

const HOUR_MS = 3_600_000
const DAY_MS = 24 * HOUR_MS
/** Below this span the rail adds nothing over one flick of the wheel. */
const MIN_SPAN_MS = 48 * HOUR_MS
/** Minimum rail distance between tick labels, as a fraction of its height. */
const TICK_GAP = 0.07
/** Viewport y (px) that counts as "the top of the feed" for marker sync. */
const SYNC_OFFSET = 150

const monthFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  month: 'short',
})
const monthYearFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  month: 'short',
  year: '2-digit',
})
const dayFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  month: 'short',
  day: 'numeric',
})

interface Tick {
  frac: number
  label: string
}

interface ScrubMap {
  spanHours: number
  /** 0 = newest photo (rail top) … 1 = oldest (rail bottom). */
  fracOf: (t: number) => number
  timeOf: (frac: number) => number
  ticks: Tick[]
}

function buildMap(buckets: HistogramBucket[]): ScrubMap | null {
  if (buckets.length === 0) return null
  const n = buckets.length
  const starts = buckets.map((b) => Date.parse(b.hour))
  const counts = buckets.map((b) => b.count)
  const total = counts.reduce((a, c) => a + c, 0)
  const oldest = starts[0]
  const newest = starts[n - 1] + HOUR_MS
  if (total === 0 || newest - oldest < MIN_SPAN_MS) return null

  // cumNewer[i]: photos in buckets strictly newer than bucket i.
  const cumNewer = new Array<number>(n).fill(0)
  for (let i = n - 2; i >= 0; i--) cumNewer[i] = cumNewer[i + 1] + counts[i + 1]

  const fracOf = (t: number): number => {
    if (t >= newest) return 0
    if (t <= oldest) return 1
    // Last bucket starting at or before t; t is inside it or in the gap above.
    let lo = 0
    let hi = n - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (starts[mid] <= t) lo = mid
      else hi = mid - 1
    }
    const within =
      t < starts[lo] + HOUR_MS ? (counts[lo] * (starts[lo] + HOUR_MS - t)) / HOUR_MS : 0
    return (cumNewer[lo] + within) / total
  }

  const timeOf = (frac: number): number => {
    const target = Math.min(1, Math.max(0, frac)) * total
    for (let i = n - 1; i >= 0; i--) {
      if (target <= cumNewer[i] + counts[i]) {
        return starts[i] + HOUR_MS - (HOUR_MS * (target - cumNewer[i])) / counts[i]
      }
    }
    return oldest
  }

  // Ticks at Eastern day (or month) boundaries, thinned to a minimum rail gap.
  // Content-proportional mapping bunches quiet stretches, so even spacing in
  // time is NOT even on the rail — thin greedily from the top instead.
  const monthly = newest - oldest > 60 * DAY_MS
  const candidates: Tick[] = []
  const seen = new Set<string>()
  for (let t = newest; t >= oldest - DAY_MS; t -= 6 * HOUR_MS) {
    const key = easternDayKey(t)
    if (!key || seen.has(key)) continue
    seen.add(key)
    if (monthly && key.slice(8, 10) !== '01') continue
    const startMs = Date.parse(easternDayBounds(key).start)
    const date = new Date(startMs)
    const label = monthly
      ? key.slice(5, 7) === '01'
        ? monthYearFmt.format(date)
        : monthFmt.format(date)
      : dayFmt.format(date)
    candidates.push({ frac: fracOf(startMs), label })
  }
  const ticks: Tick[] = []
  for (const c of candidates) {
    if (c.frac < TICK_GAP / 2 || c.frac > 1 - TICK_GAP / 2) continue
    if (ticks.length === 0 || c.frac - ticks[ticks.length - 1].frac >= TICK_GAP) {
      ticks.push(c)
    }
  }

  return { spanHours: (newest - oldest) / HOUR_MS, fracOf, timeOf, ticks }
}

interface TimeScrubberProps {
  buckets: HistogramBucket[]
  onJump: (iso: string) => void
}

export default function TimeScrubber({ buckets, onJump }: TimeScrubberProps) {
  const map = useMemo(() => buildMap(buckets), [buckets])
  const railRef = useRef<HTMLDivElement>(null)
  const [markerFrac, setMarkerFrac] = useState(0)
  const [dragFrac, setDragFrac] = useState<number | null>(null)

  // While the rail is up it IS the scrollbar — hide the native one so two
  // vertical indicators don't compete for the same edge (styles.css keys off
  // this class). Wheel/trackpad/keyboard scrolling are unaffected.
  useEffect(() => {
    if (!map) return
    document.documentElement.classList.add('has-scrubber')
    return () => document.documentElement.classList.remove('has-scrubber')
  }, [map])

  // Follow the scroll: the topmost visible day section drives the marker.
  useEffect(() => {
    if (!map) return
    let raf = 0
    const sync = () => {
      raf = 0
      const sections = document.querySelectorAll<HTMLElement>('.grid-wrap section[data-day]')
      for (const s of sections) {
        if (s.getBoundingClientRect().bottom > SYNC_OFFSET) {
          const day = s.dataset.day
          if (day) {
            const b = easternDayBounds(day)
            setMarkerFrac(map.fracOf((Date.parse(b.start) + Date.parse(b.end)) / 2))
          }
          return
        }
      }
    }
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(sync)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    sync()
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [map])

  if (!map) return null

  const fracFromEvent = (e: ReactPointerEvent<HTMLDivElement>): number => {
    const rect = e.currentTarget.getBoundingClientRect()
    return Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height))
  }

  const handlePointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragFrac(fracFromEvent(e))
  }
  const handlePointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (dragFrac !== null) setDragFrac(fracFromEvent(e))
  }
  const handlePointerUp = () => {
    if (dragFrac === null) return
    onJump(new Date(map.timeOf(dragFrac)).toISOString())
    setDragFrac(null)
  }

  const shownFrac = dragFrac ?? markerFrac

  return (
    <div
      ref={railRef}
      className="scrubber"
      role="slider"
      aria-label="Timeline — drag to jump to a point in time"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(shownFrac * 100)}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={() => setDragFrac(null)}
    >
      <div className="scrubber-track" aria-hidden="true" />
      {map.ticks.map((t) => (
        <div
          key={`${t.frac}-${t.label}`}
          className="scrubber-tick"
          style={{ top: `${t.frac * 100}%` }}
          aria-hidden="true"
        >
          {t.label}
        </div>
      ))}
      <div
        className={`scrubber-marker${dragFrac !== null ? ' dragging' : ''}`}
        style={{ top: `${shownFrac * 100}%` }}
        aria-hidden="true"
      />
      {dragFrac !== null && (
        <div className="scrubber-bubble" style={{ top: `${dragFrac * 100}%` }}>
          {chartTimeLabel(map.timeOf(dragFrac), map.spanHours, true)}
        </div>
      )}
    </div>
  )
}
