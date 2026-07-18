import { X } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { chartTimeLabel } from '../format'
import type { HistogramBucket } from '../types'

/**
 * The photos-page activity strip: a histogram of captures over time (hourly
 * buckets from /photos/histogram, re-binned to screen resolution), with a
 * pointer brush that filters the feed to the dragged range. A bare click
 * clears the range. Geometry and pointer handling follow SurveyTimeline.tsx —
 * this is the survey timeline's trick applied to the feed's noise problem:
 * see where the activity is, jump straight to it.
 */

interface PhotoTimelineProps {
  /** Hourly counts under the active filters (non-empty hours only, sorted). */
  buckets: HistogramBucket[]
  /** Active brushed range (ISO instants), shown as shading. */
  from: string | null
  to: string | null
  onBrush: (from: string, to: string) => void
  onClear: () => void
}

const W = 640
const H = 88
const PAD = { top: 8, right: 12, bottom: 18, left: 12 } as const
const BUCKETS = 120
const MIN_BRUSH_PX = 6 // smaller drags are treated as stray clicks (= clear)
const HOUR_MS = 3_600_000

export default function PhotoTimeline({
  buckets,
  from,
  to,
  onBrush,
  onClear,
}: PhotoTimelineProps) {
  const [drag, setDrag] = useState<{ startT: number; endT: number } | null>(null)

  const geom = useMemo(() => {
    if (buckets.length === 0) return null
    const first = Date.parse(buckets[0].hour)
    const last = Date.parse(buckets[buckets.length - 1].hour) + HOUR_MS
    let xMin = first
    let xMax = last
    if (xMax - xMin < HOUR_MS * 2) {
      xMin -= HOUR_MS
      xMax += HOUR_MS
    }
    const pad = (xMax - xMin) * 0.02
    xMin -= pad
    xMax += pad
    const innerW = W - PAD.left - PAD.right
    const histH = H - PAD.top - PAD.bottom
    const bucketMs = (xMax - xMin) / BUCKETS
    const counts = new Array<number>(BUCKETS).fill(0)
    for (const b of buckets) {
      const i = Math.min(Math.floor((Date.parse(b.hour) - xMin) / bucketMs), BUCKETS - 1)
      counts[i] += b.count
    }
    const maxCount = Math.max(...counts, 1)
    const xOf = (t: number) => PAD.left + ((t - xMin) / (xMax - xMin)) * innerW
    const tOf = (x: number) => xMin + ((x - PAD.left) / innerW) * (xMax - xMin)
    return { xMin, xMax, counts, maxCount, xOf, tOf, histH, bucketMs }
  }, [buckets])

  if (!geom) return null

  const spanHours = (geom.xMax - geom.xMin) / HOUR_MS
  const histBottom = PAD.top + geom.histH

  const toSvgX = (e: ReactPointerEvent<SVGSVGElement>): number => {
    const rect = e.currentTarget.getBoundingClientRect()
    return ((e.clientX - rect.left) / rect.width) * W
  }

  const handlePointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
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
    if (geom.xOf(b) - geom.xOf(a) < MIN_BRUSH_PX) {
      onClear()
      return
    }
    const clamp = (t: number) => Math.min(Math.max(t, geom.xMin), geom.xMax)
    onBrush(new Date(clamp(a)).toISOString(), new Date(clamp(b)).toISOString())
  }

  // In-flight drag previews live; otherwise the committed range shades.
  let shade: { a: number; b: number } | null = null
  if (drag) {
    shade = { a: Math.min(drag.startT, drag.endT), b: Math.max(drag.startT, drag.endT) }
  } else if (from && to) {
    shade = { a: Date.parse(from), b: Date.parse(to) }
  }

  return (
    <div className="timeline-wrap">
      <svg
        className="chart-svg survey-timeline photo-timeline"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Photo activity timeline — drag to filter a time range"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => setDrag(null)}
      >
        {shade && (
          <rect
            className="survey-shade"
            x={geom.xOf(shade.a)}
            y={PAD.top}
            width={Math.max(geom.xOf(shade.b) - geom.xOf(shade.a), 2)}
            height={geom.histH}
          />
        )}
        {geom.counts.map((count, i) =>
          count === 0 ? null : (
            <rect
              key={i}
              className="survey-bar"
              x={geom.xOf(geom.xMin + i * geom.bucketMs)}
              y={histBottom - (count / geom.maxCount) * (geom.histH - 4)}
              width={Math.max((W - PAD.left - PAD.right) / BUCKETS - 0.5, 1)}
              height={(count / geom.maxCount) * (geom.histH - 4)}
            />
          ),
        )}
        <line
          className="chart-grid"
          x1={PAD.left}
          x2={W - PAD.right}
          y1={histBottom}
          y2={histBottom}
        />
        <text className="chart-axis-text" x={PAD.left} y={H - 5} textAnchor="start">
          {chartTimeLabel(geom.xMin, spanHours)}
        </text>
        <text className="chart-axis-text" x={W - PAD.right} y={H - 5} textAnchor="end">
          {chartTimeLabel(geom.xMax, spanHours)}
        </text>
      </svg>
      {from && to && !drag && (
        <button
          type="button"
          className="btn timeline-clear"
          onClick={onClear}
          aria-label="Clear time range filter"
        >
          {chartTimeLabel(Date.parse(from), spanHours, true)} –{' '}
          {chartTimeLabel(Date.parse(to), spanHours, true)}
          <X size={13} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
