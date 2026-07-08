import { useMemo, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { chartTimeLabel } from '../format'

export interface ChartSeriesDef {
  label: string
  color: string
  /** Unit suffix for readouts, e.g. " V", " dBm". */
  unit: string
  /** Decimal places for readout values. */
  decimals: number
  /** Render as a thinner, dimmer companion line (e.g. SNR next to RSSI). */
  faint?: boolean
  /** One value per entry in `times`; null = gap in the line. */
  values: (number | null)[]
}

interface TelemetryChartProps {
  title: string
  /** One-line plain-language explanation of what the chart measures. */
  hint?: string
  /** Epoch ms, ascending, shared by every series. */
  times: number[]
  series: ChartSeriesDef[]
  /** Selected range, used to pick time-label granularity. */
  spanHours: number
  /** Decimal places for the y-axis min/max labels. */
  axisDecimals?: number
}

// SVG viewBox geometry — rendered at width:100%, aspect preserved.
const W = 640
const H = 210
const PAD = { top: 14, right: 16, bottom: 26, left: 54 } as const

/** Build a polyline path, lifting the pen across null gaps. */
function buildPath(
  values: (number | null)[],
  xOf: (i: number) => number,
  yOf: (v: number) => number,
): string {
  let d = ''
  let pen = false
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null) {
      pen = false
      continue
    }
    d += `${pen ? 'L' : 'M'}${xOf(i).toFixed(1)} ${yOf(v).toFixed(1)}`
    pen = true
  }
  return d
}

function lastNonNullIndex(values: (number | null)[]): number {
  for (let i = values.length - 1; i >= 0; i--) {
    if (values[i] != null) return i
  }
  return -1
}

export default function TelemetryChart({
  title,
  hint,
  times,
  series,
  spanHours,
  axisDecimals = 1,
}: TelemetryChartProps) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  const geom = useMemo(() => {
    if (times.length === 0) return null
    let dataMin = Infinity
    let dataMax = -Infinity
    for (const s of series) {
      for (const v of s.values) {
        if (v == null) continue
        if (v < dataMin) dataMin = v
        if (v > dataMax) dataMax = v
      }
    }
    if (!Number.isFinite(dataMin)) return null
    const xMin = times[0]
    const xMax = times[times.length - 1] > xMin ? times[times.length - 1] : xMin + 1
    const yPad = (dataMax - dataMin) * 0.08 || Math.max(Math.abs(dataMax) * 0.02, 0.5)
    const yMin = dataMin - yPad
    const yMax = dataMax + yPad
    const innerW = W - PAD.left - PAD.right
    const innerH = H - PAD.top - PAD.bottom
    const xOf = (t: number) => PAD.left + ((t - xMin) / (xMax - xMin)) * innerW
    const yOf = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * innerH
    return { xMin, xMax, dataMin, dataMax, xOf, yOf }
  }, [times, series])

  // Guard against a stale hover index after a range switch shrinks the data.
  const hover = hoverIdx != null && hoverIdx < times.length ? hoverIdx : null

  const fmt = (v: number, s: ChartSeriesDef) => `${v.toFixed(s.decimals)}${s.unit}`

  const readoutFor = (idx: number): string => {
    const parts: string[] = []
    for (const s of series) {
      const v = s.values[idx]
      if (v == null) continue
      parts.push(series.length > 1 ? `${s.label} ${fmt(v, s)}` : fmt(v, s))
    }
    const when = chartTimeLabel(times[idx], spanHours, true)
    return parts.length > 0 ? `${when} · ${parts.join(' · ')}` : when
  }

  const latestIdx = Math.max(...series.map((s) => lastNonNullIndex(s.values)))
  const readout =
    hover != null ? readoutFor(hover) : latestIdx >= 0 ? readoutFor(latestIdx) : ''

  const handlePointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!geom || times.length === 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    if (rect.width === 0) return
    const px = ((e.clientX - rect.left) / rect.width) * W
    const innerW = W - PAD.left - PAD.right
    const t = geom.xMin + ((px - PAD.left) / innerW) * (geom.xMax - geom.xMin)
    let best = 0
    let bestDist = Infinity
    for (let i = 0; i < times.length; i++) {
      const dist = Math.abs(times[i] - t)
      if (dist < bestDist) {
        bestDist = dist
        best = i
      }
    }
    setHoverIdx(best)
  }

  return (
    <section className="chart-box" aria-label={title}>
      <div className="chart-head">
        <span className="chart-title">{title}</span>
        {geom && <span className="chart-readout">{readout}</span>}
      </div>

      {hint && <div className="chart-hint">{hint}</div>}

      {series.length > 1 && geom && (
        <div className="chart-legend" aria-hidden="true">
          {series.map((s) => (
            <span key={s.label} className="chart-key">
              <span
                className="chart-key-swatch"
                style={{ background: s.color, opacity: s.faint ? 0.55 : 1 }}
              />
              {s.label}
            </span>
          ))}
        </div>
      )}

      {!geom ? (
        <div className="chart-empty">No data in this range</div>
      ) : (
        <svg
          className="chart-svg"
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`${title} chart`}
          onPointerMove={handlePointerMove}
          onPointerLeave={() => setHoverIdx(null)}
        >
          {/* Min/max gridlines + y labels (skip the min label when they'd collide). */}
          <line
            className="chart-grid"
            x1={PAD.left}
            x2={W - PAD.right}
            y1={geom.yOf(geom.dataMax)}
            y2={geom.yOf(geom.dataMax)}
          />
          <text
            className="chart-axis-text"
            x={PAD.left - 8}
            y={geom.yOf(geom.dataMax)}
            textAnchor="end"
            dominantBaseline="middle"
          >
            {geom.dataMax.toFixed(axisDecimals)}
          </text>
          {geom.yOf(geom.dataMin) - geom.yOf(geom.dataMax) > 14 && (
            <>
              <line
                className="chart-grid"
                x1={PAD.left}
                x2={W - PAD.right}
                y1={geom.yOf(geom.dataMin)}
                y2={geom.yOf(geom.dataMin)}
              />
              <text
                className="chart-axis-text"
                x={PAD.left - 8}
                y={geom.yOf(geom.dataMin)}
                textAnchor="end"
                dominantBaseline="middle"
              >
                {geom.dataMin.toFixed(axisDecimals)}
              </text>
            </>
          )}

          {/* First/last time labels. */}
          <text className="chart-axis-text" x={PAD.left} y={H - 8} textAnchor="start">
            {chartTimeLabel(times[0], spanHours)}
          </text>
          <text
            className="chart-axis-text"
            x={W - PAD.right}
            y={H - 8}
            textAnchor="end"
          >
            {chartTimeLabel(times[times.length - 1], spanHours)}
          </text>

          {/* Hover hairline. */}
          {hover != null && (
            <line
              className="chart-hairline"
              x1={geom.xOf(times[hover])}
              x2={geom.xOf(times[hover])}
              y1={PAD.top}
              y2={H - PAD.bottom}
            />
          )}

          {/* Series lines. */}
          {series.map((s) => (
            <path
              key={s.label}
              d={buildPath(s.values, (i) => geom.xOf(times[i]), geom.yOf)}
              fill="none"
              stroke={s.color}
              strokeWidth={s.faint ? 1.5 : 2}
              strokeOpacity={s.faint ? 0.55 : 1}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}

          {/* Dot on the latest point of each series (surface ring keeps it legible). */}
          {series.map((s) => {
            const i = lastNonNullIndex(s.values)
            if (i < 0) return null
            const v = s.values[i] as number
            return (
              <circle
                key={s.label}
                className="chart-dot"
                cx={geom.xOf(times[i])}
                cy={geom.yOf(v)}
                r={s.faint ? 3 : 4}
                fill={s.color}
                fillOpacity={s.faint ? 0.7 : 1}
              />
            )
          })}

          {/* Hovered points. */}
          {hover != null &&
            series.map((s) => {
              const v = s.values[hover]
              if (v == null) return null
              return (
                <circle
                  key={s.label}
                  className="chart-dot"
                  cx={geom.xOf(times[hover])}
                  cy={geom.yOf(v)}
                  r={3.5}
                  fill={s.color}
                />
              )
            })}
        </svg>
      )}
    </section>
  )
}
