import { useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { chartTimeLabel, easternDayKey } from '../format'
import type { Probe, ProbeSession } from '../types'

/**
 * The survey timeline: a histogram of button presses over received_at (the
 * server clock — the board's RTC is not trusted), with the server-derived
 * sessions drawn as clickable blocks beneath it. Clicking a block filters
 * the map to that session; dragging across the histogram brushes an
 * arbitrary range (the gap heuristic will not always agree with reality).
 *
 * Geometry and pointer handling follow TelemetryChart.tsx.
 */

interface SurveyTimelineProps {
  /** Every probe, no-fix ones included — presses are presses. */
  probes: Probe[]
  sessions: ProbeSession[]
  /** Selected session index (the range being viewed). */
  session: number | null
  /** Baseline session index in compare mode. */
  compare: number | null
  /** Hand-brushed range (wins over `session` for map filtering). */
  range: { from: string; to: string } | null
  onSelectSession: (index: number | null) => void
  onBrush: (from: string, to: string) => void
}

const W = 640
const H = 168
const PAD = { top: 10, right: 12, bottom: 20, left: 12 } as const
const BLOCKS_H = 22 // session-block strip above the time axis
const BUCKETS = 120
const MIN_BRUSH_PX = 6 // smaller drags are treated as stray clicks

export default function SurveyTimeline({
  probes,
  sessions,
  session,
  compare,
  range,
  onSelectSession,
  onBrush,
}: SurveyTimelineProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [drag, setDrag] = useState<{ startT: number; endT: number } | null>(null)

  const geom = useMemo(() => {
    if (probes.length === 0) return null
    const times = probes.map((p) => Date.parse(p.received_at)).sort((a, b) => a - b)
    let xMin = times[0]
    let xMax = times[times.length - 1]
    if (xMax === xMin) {
      xMin -= 30 * 60_000
      xMax += 30 * 60_000
    }
    // Breathing room so edge sessions don't touch the frame.
    const pad = (xMax - xMin) * 0.02
    xMin -= pad
    xMax += pad
    const innerW = W - PAD.left - PAD.right
    const histH = H - PAD.top - PAD.bottom - BLOCKS_H
    const bucketMs = (xMax - xMin) / BUCKETS
    const counts = new Array<number>(BUCKETS).fill(0)
    for (const t of times) {
      const i = Math.min(Math.floor((t - xMin) / bucketMs), BUCKETS - 1)
      counts[i] += 1
    }
    const maxCount = Math.max(...counts, 1)
    const xOf = (t: number) => PAD.left + ((t - xMin) / (xMax - xMin)) * innerW
    const tOf = (x: number) => xMin + ((x - PAD.left) / innerW) * (xMax - xMin)
    return { xMin, xMax, counts, maxCount, xOf, tOf, histH, bucketMs }
  }, [probes])

  if (!geom) {
    return <div className="chart-empty">No probes yet — walk the property and press the button</div>
  }

  const spanHours = (geom.xMax - geom.xMin) / 3_600_000
  const histBottom = PAD.top + geom.histH
  const blocksTop = histBottom + 4

  const toSvgX = (e: ReactPointerEvent<SVGSVGElement>): number => {
    const rect = e.currentTarget.getBoundingClientRect()
    return ((e.clientX - rect.left) / rect.width) * W
  }

  const handlePointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    // Session blocks handle their own clicks; only brush over the histogram.
    const rect = e.currentTarget.getBoundingClientRect()
    const y = ((e.clientY - rect.top) / rect.height) * H
    if (y > histBottom) return
    e.currentTarget.setPointerCapture(e.pointerId)
    const t = geom.tOf(toSvgX(e))
    setDrag({ startT: t, endT: t })
  }

  const handlePointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag) return
    setDrag({ ...drag, endT: geom.tOf(toSvgX(e)) })
  }

  const handlePointerUp = () => {
    if (!drag) return
    const [a, b] = drag.startT < drag.endT ? [drag.startT, drag.endT] : [drag.endT, drag.startT]
    setDrag(null)
    const widthPx = geom.xOf(b) - geom.xOf(a)
    if (widthPx < MIN_BRUSH_PX) return
    const clamp = (t: number) => Math.min(Math.max(t, geom.xMin), geom.xMax)
    onBrush(new Date(clamp(a)).toISOString(), new Date(clamp(b)).toISOString())
  }

  // The active selection, as a shaded span: an in-flight drag previews live,
  // then the committed brush range, then the selected session.
  let shade: { a: number; b: number } | null = null
  if (drag) {
    shade = { a: Math.min(drag.startT, drag.endT), b: Math.max(drag.startT, drag.endT) }
  } else if (range) {
    shade = { a: Date.parse(range.from), b: Date.parse(range.to) }
  } else if (session != null && sessions[session]) {
    shade = {
      a: Date.parse(sessions[session].started_at),
      b: Date.parse(sessions[session].ended_at),
    }
  }

  return (
    <svg
      ref={svgRef}
      className="chart-svg survey-timeline"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="Probe timeline"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={() => setDrag(null)}
    >
      {/* Selection shading spans histogram + blocks. */}
      {shade && (
        <rect
          className="survey-shade"
          x={geom.xOf(shade.a)}
          y={PAD.top}
          width={Math.max(geom.xOf(shade.b) - geom.xOf(shade.a), 2)}
          height={geom.histH + 4 + BLOCKS_H}
        />
      )}

      {/* Histogram of presses — the clusters are the sessions, visibly. */}
      {geom.counts.map((count, i) =>
        count === 0 ? null : (
          <rect
            key={i}
            className="survey-bar"
            x={geom.xOf(geom.xMin + i * geom.bucketMs)}
            y={histBottom - (count / geom.maxCount) * (geom.histH - 6)}
            width={Math.max((W - PAD.left - PAD.right) / BUCKETS - 0.5, 1)}
            height={(count / geom.maxCount) * (geom.histH - 6)}
          />
        ),
      )}
      <line className="chart-grid" x1={PAD.left} x2={W - PAD.right} y1={histBottom} y2={histBottom} />

      {/* Session blocks: click to filter the map to that cluster. A block is
          a navigation handle, not a measurement (the histogram carries the
          time truth), so short sessions get a readable minimum width and
          neighbours are nudged apart instead of overlapping. Labels are
          when-the-walk-happened (chartTimeLabel picks date vs time-of-day by
          span) — nobody recognizes their walks by a sequence number. */}
      {(() => {
        const MIN_W = 46
        let prevEnd = -Infinity
        const rects = sessions.map((s) => {
          let x1 = geom.xOf(Date.parse(s.started_at))
          const w = Math.max(geom.xOf(Date.parse(s.ended_at)) - x1, MIN_W)
          x1 = Math.max(x1, prevEnd + 2)
          prevEnd = x1 + w
          return { s, x1, w }
        })
        // Clamp to the right edge, cascading left so neighbours never stack.
        for (let i = rects.length - 1; i >= 0; i--) {
          const limit = (i === rects.length - 1 ? W - PAD.right : rects[i + 1].x1 - 2) - rects[i].w
          rects[i].x1 = Math.min(rects[i].x1, limit)
        }
        return rects
      })().map(({ s, x1, w }) => {
        const active = session === s.index
        const isBaseline = compare === s.index
        // Two walks on the same day would both read "Jul 10" — those switch
        // to time-of-day (passing spanHours=1 forces the time form).
        const sameDaySiblings =
          sessions.filter((o) => easternDayKey(o.started_at) === easternDayKey(s.started_at))
            .length > 1
        const label = chartTimeLabel(Date.parse(s.started_at), sameDaySiblings ? 1 : spanHours)
        return (
          <g
            key={s.index}
            className={`survey-block${active ? ' active' : ''}${isBaseline ? ' baseline' : ''}`}
            onClick={() => onSelectSession(active ? null : s.index)}
            role="button"
            aria-label={`Walk ${label}: ${s.count} probes${isBaseline ? ' (baseline)' : ''}`}
          >
            <rect x={x1} y={blocksTop} width={w} height={BLOCKS_H - 6} rx={3} />
            <text x={x1 + w / 2} y={blocksTop + (BLOCKS_H - 6) / 2} textAnchor="middle" dominantBaseline="central">
              {isBaseline ? `${label} ⚑` : label}
            </text>
          </g>
        )
      })}

      {/* First/last time labels. */}
      <text className="chart-axis-text" x={PAD.left} y={H - 6} textAnchor="start">
        {chartTimeLabel(geom.xMin, spanHours)}
      </text>
      <text className="chart-axis-text" x={W - PAD.right} y={H - 6} textAnchor="end">
        {chartTimeLabel(geom.xMax, spanHours)}
      </text>
    </svg>
  )
}
