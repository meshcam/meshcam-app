import { Crosshair, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import { UnauthorizedError } from '../api'
import { chartTimeLabel, formatEastern } from '../format'
import { idMatchesRef } from '../ids'
import { navigate, surveyUrl } from '../router'
import type { SurveyQuery } from '../router'
import {
  compareSessions,
  DEFAULT_MATCH_RADIUS_M,
  deltaColor,
  DELTA_BANDS,
  formatDbm,
  MIN_PAIRS_FOR_MEDIAN,
  resolveTiles,
  RSSI_BANDS,
  rssiColor,
} from '../survey'
import type { Camera, Me, Probe, ProbeSession } from '../types'
import SurveyMap from './SurveyMap'
import type { MapMarker } from './SurveyMap'
import SurveyTimeline from './SurveyTimeline'

/**
 * /survey — the before/after comparison tool that happens to be rendered on
 * a map. The URL is the whole state (see SurveyQuery in router.ts): pick a
 * session (or brush a range), pick a baseline, send someone the link.
 */

interface SurveyViewProps {
  me: Me
  /** Read-only demo: the gateway pin-drop (the view's one write) is hidden. */
  demo: boolean
  query: SurveyQuery
  onUnauthorized: () => void
}

const inRange = (p: Probe, a: number, b: number) => {
  const t = Date.parse(p.received_at)
  return t >= a && t <= b
}

export default function SurveyView({ me, demo, query, onUnauthorized }: SurveyViewProps) {
  const [probes, setProbes] = useState<Probe[] | null>(null)
  const [sessions, setSessions] = useState<ProbeSession[]>([])
  const [cameras, setCameras] = useState<Camera[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pinMode, setPinMode] = useState(false)
  const [showNoFix, setShowNoFix] = useState(false)

  const gap = query.gap ?? 30
  const radius = query.radius ?? DEFAULT_MATCH_RADIUS_M

  const setQ = useCallback(
    (patch: Partial<SurveyQuery>) => {
      navigate(surveyUrl({ ...query, ...patch }), { replace: true })
    },
    [query],
  )

  const fail = useCallback(
    (err: unknown) => {
      if (err instanceof UnauthorizedError) onUnauthorized()
      else setError(err instanceof Error ? err.message : 'Failed to load survey data')
    },
    [onUnauthorized],
  )

  // All probes, one fetch — the timeline always shows the full history and
  // filtering is cheap client-side (a survey is hundreds of rows, not more).
  useEffect(() => {
    let cancelled = false
    setError(null)
    api
      .getProbes({ fix: 'all' })
      .then((data) => {
        if (!cancelled) setProbes(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) fail(err)
      })
    return () => {
      cancelled = true
    }
  }, [fail])

  // Sessions are server-derived (they must match what other viewers of this
  // link see), scoped to the node filter and gap setting.
  useEffect(() => {
    let cancelled = false
    api
      .getProbeSessions(query.node ?? undefined, gap)
      .then((data) => {
        if (!cancelled) setSessions(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) fail(err)
      })
    return () => {
      cancelled = true
    }
  }, [query.node, gap, fail])

  useEffect(() => {
    let cancelled = false
    api
      .getCameras()
      .then((data) => {
        if (!cancelled) setCameras(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) fail(err)
      })
    return () => {
      cancelled = true
    }
  }, [fail])

  // --- derived state --------------------------------------------------------

  const nodeProbes = useMemo(() => {
    if (!probes) return []
    if (!query.node) return probes
    return probes.filter((p) => idMatchesRef(p.node_id, query.node as string))
  }, [probes, query.node])

  const surveyorIds = useMemo(
    () => new Set((probes ?? []).map((p) => p.node_id)),
    [probes],
  )

  // Selection: a brushed range wins, then the selected session, else all.
  const selection = useMemo((): { a: number; b: number } | null => {
    if (query.from && query.to) return { a: Date.parse(query.from), b: Date.parse(query.to) }
    const s = query.session != null ? sessions[query.session] : undefined
    if (s) return { a: Date.parse(s.started_at), b: Date.parse(s.ended_at) }
    return null
  }, [query.from, query.to, query.session, sessions])

  const visible = useMemo(
    () => (selection ? nodeProbes.filter((p) => inRange(p, selection.a, selection.b)) : nodeProbes),
    [nodeProbes, selection],
  )
  const visibleFix = useMemo(() => visible.filter((p) => p.fix_ok), [visible])
  const visibleNoFix = useMemo(() => visible.filter((p) => !p.fix_ok), [visible])

  // Compare mode: judge the selected range against a baseline session.
  const baselineSession = query.compare != null ? sessions[query.compare] : undefined
  const baselineFix = useMemo(() => {
    if (!baselineSession) return []
    const a = Date.parse(baselineSession.started_at)
    const b = Date.parse(baselineSession.ended_at)
    return nodeProbes.filter((p) => p.fix_ok && inRange(p, a, b))
  }, [nodeProbes, baselineSession])

  const comparing = baselineSession !== undefined && selection !== null
  const compareResult = useMemo(
    () => (comparing ? compareSessions(visibleFix, baselineFix, radius) : null),
    [comparing, visibleFix, baselineFix, radius],
  )

  const markers = useMemo((): MapMarker[] => {
    if (compareResult) {
      const dots: MapMarker[] = baselineFix.map((probe) => ({
        probe,
        color: '#8a8f98',
        dim: true,
      }))
      for (const m of compareResult.matched) {
        dots.push(
          m.deltaDb === null
            ? {
                probe: m.probe,
                color: '#8a8f98',
                hollow: true,
                extraHtml: '<div class="probe-popup-note">no baseline within match radius</div>',
              }
            : {
                probe: m.probe,
                color: deltaColor(m.deltaDb),
                extraHtml: `<div class="probe-popup-note">Δ ${m.deltaDb > 0 ? '+' : ''}${m.deltaDb.toFixed(1)} dB vs baseline ${m.distanceM?.toFixed(0)} m away</div>`,
              },
        )
      }
      return dots
    }
    return visibleFix.map((probe) => ({ probe, color: rssiColor(probe.gw_rssi) }))
  }, [compareResult, baselineFix, visibleFix])

  const gateways = useMemo(
    () =>
      cameras
        .filter((c) => c.kind === 'gateway' && c.lat != null && c.lon != null)
        .map((c) => ({ lat: c.lat as number, lon: c.lon as number, name: c.name })),
    [cameras],
  )
  // Sites can have their own gateways (home + parents), so the place button
  // must say WHICH gateway the next map click will position.
  const unplacedGateway = useMemo(
    () => cameras.find((c) => c.kind === 'gateway' && (c.lat == null || c.lon == null)),
    [cameras],
  )

  const handlePinDrop = useCallback(
    (lat: number, lon: number) => {
      if (!unplacedGateway) return
      setPinMode(false)
      api
        .patchCamera(unplacedGateway.id, {
          lat: Number(lat.toFixed(6)),
          lon: Number(lon.toFixed(6)),
        })
        .then((updated) =>
          setCameras((prev) => prev.map((c) => (c.id === updated.id ? updated : c))),
        )
        .catch(fail)
    },
    [unplacedGateway, fail],
  )

  // --- render ----------------------------------------------------------------

  if (error) {
    return <div className="survey-view"><div className="feed-error">{error}</div></div>
  }
  if (probes === null) {
    return (
      <div className="app-loading" aria-label="Loading">
        <span className="spinner" />
      </div>
    )
  }

  const tiles = resolveTiles(me.map_tile_url, me.map_tile_attribution)
  const legendBands = compareResult ? DELTA_BANDS : RSSI_BANDS
  const surveyors = cameras.filter((c) => surveyorIds.has(c.id))
  // Walks are named by when they happened, not by index — the S1/S2 numbers
  // confused their very first real user. "3/14 mappable" warns up front that
  // a (bench) session will barely change the map — most of its presses have
  // no trustworthy GPS fix.
  const sessionLabel = (s: ProbeSession) =>
    `the ${chartTimeLabel(Date.parse(s.started_at), 999, true)} walk · ${
      s.fix_count === s.count ? `${s.count} probes` : `${s.fix_count}/${s.count} mappable`
    }`

  return (
    <div className="survey-view">
      <details className="survey-about">
        <summary>What is the survey map?</summary>
        <p>
          A <b>surveyor</b> is a handheld LoRa probe you walk the property
          with: a{' '}
          <a
            href="https://lilygo.cc/en-us/products/t-beam"
            target="_blank"
            rel="noreferrer"
          >
            LILYGO T-Beam
          </a>{' '}
          (~$35 — the classic v1.2 in your region's band, 915&nbsp;MHz in the
          US; not the S3 or Supreme variants) running the{' '}
          <a
            href="https://github.com/meshcam/meshcam-firmware/tree/master/surveyor"
            target="_blank"
            rel="noreferrer"
          >
            surveyor firmware
          </a>
          . Every button press sends a real thumbnail-sized transfer through
          the mesh and records how well the gateway heard it from that spot —
          a proof-of-connection at GPS coordinates, not a guess.
        </p>
        <p>
          Dots are coloured by <b>gateway-side RSSI</b>, the one honest
          instrument in the system (SNR reads ~13&nbsp;dB everywhere and is
          shown only in popups). Presses cluster into <b>sessions</b> on the
          timeline below. The point of all of it: move an antenna or add a
          relay, walk the same loop again, pick the old walk as the{' '}
          <b>baseline</b> — and see in dB whether it actually helped. With
          fewer than {MIN_PAIRS_FOR_MEDIAN} matched positions it refuses to
          call a trend, and presses without a GPS fix are counted but never
          plotted (their coordinates are fiction).
        </p>
      </details>

      <div className="survey-toolbar">
        {surveyors.length > 1 && (
          <select
            className="camera-select survey-select"
            aria-label="Surveyor filter"
            value={query.node ?? ''}
            onChange={(e) =>
              setQ({ node: e.target.value || null, session: null, compare: null, from: null, to: null })
            }
          >
            <option value="">All surveyors</option>
            {surveyors.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}

        {/* The toolbar reads as a sentence — "Show <walk> vs <baseline>" —
            because both selects list the same walks (the feature IS
            walk-vs-walk comparison) and without the connecting words they
            look like duplicates. The Show select also mirrors the timeline
            blocks, which live below the fold. */}
        <label className="survey-field">
          Show
          <select
            className="camera-select survey-select"
            aria-label="Walk shown on the map"
            value={query.from && query.to ? 'range' : (query.session ?? '')}
            onChange={(e) =>
              setQ({
                session: e.target.value === '' || e.target.value === 'range' ? null : Number(e.target.value),
                from: null,
                to: null,
              })
            }
          >
            <option value="">every probe</option>
            {query.from && query.to && (
              <option value="range" disabled>
                brushed time range
              </option>
            )}
            {sessions.map((s) => (
              <option key={s.index} value={s.index} disabled={s.index === query.compare}>
                {sessionLabel(s)}
              </option>
            ))}
          </select>
        </label>

        <label className="survey-field">
          vs
          <select
            className="camera-select survey-select"
            aria-label="Baseline walk to compare against"
            value={query.compare ?? ''}
            onChange={(e) => {
              const idx = e.target.value === '' ? null : Number(e.target.value)
              if (idx != null && query.session == null && !(query.from && query.to)) {
                // A baseline with nothing to judge against it does nothing —
                // default the shown side to the latest other walk so the
                // comparison appears immediately instead of silently arming.
                const current = [...sessions].reverse().find((s) => s.index !== idx)
                setQ({ compare: idx, session: current?.index ?? null, from: null, to: null })
              } else {
                setQ({ compare: idx })
              }
            }}
          >
            <option value="">nothing — colour by dBm</option>
            {sessions.map((s) => (
              <option key={s.index} value={s.index} disabled={s.index === query.session}>
                {sessionLabel(s)}
              </option>
            ))}
          </select>
        </label>

        {(selection || query.compare != null) && (
          <button
            type="button"
            className="btn"
            onClick={() => setQ({ session: null, compare: null, from: null, to: null })}
          >
            <X size={14} aria-hidden="true" /> Clear selection
          </button>
        )}

        <span className="survey-count">
          {visibleFix.length} probe{visibleFix.length === 1 ? '' : 's'} on map
          {visibleNoFix.length > 0 && (
            <button
              type="button"
              className={`no-fix-badge${showNoFix ? ' active' : ''}`}
              onClick={() => setShowNoFix((v) => !v)}
              title="Probes without a trustworthy GPS fix — real presses, fictional coordinates; never plotted"
            >
              +{visibleNoFix.length} no fix
            </button>
          )}
        </span>

        {!demo && unplacedGateway && (
          <button
            type="button"
            className={`btn${pinMode ? ' active' : ''}`}
            onClick={() => setPinMode((v) => !v)}
            title={`${unplacedGateway.name} has no position yet — click the map to set it (or paste coordinates in Settings)`}
          >
            <Crosshair size={14} aria-hidden="true" />
            {pinMode ? `Click the map to place ${unplacedGateway.name}…` : `Place ${unplacedGateway.name}`}
          </button>
        )}
      </div>

      {query.compare != null && !comparing && (
        <div className="survey-compare-card">
          <span className="survey-median guard">
            baseline set ({baselineSession ? `the ${formatEastern(baselineSession.started_at)} walk` : 'a walk'}) —
            pick a walk under "Show" (or brush the timeline) to compare against it
          </span>
        </div>
      )}

      {comparing && compareResult && (
        <div className="survey-compare-card">
          {compareResult.medianDeltaDb !== null ? (
            <span className="survey-median" style={{ color: deltaColor(compareResult.medianDeltaDb) }}>
              median {compareResult.medianDeltaDb > 0 ? '+' : ''}
              {compareResult.medianDeltaDb.toFixed(1)} dB
            </span>
          ) : (
            // The honesty guard: a confident number off 2 samples is worse
            // than no number. Say exactly why there is no median.
            <span className="survey-median guard">
              no median — only {compareResult.pairCount} of the probes in this
              selection {compareResult.pairCount === 1 ? 'has' : 'have'} a baseline
              within {radius} m; at least {MIN_PAIRS_FOR_MEDIAN} matched pairs are
              needed before a trend is worth stating
            </span>
          )}
          <span>
            {compareResult.pairCount} matched · {compareResult.unmatchedCount} without baseline
          </span>
          <label className="survey-radius">
            match radius{' '}
            <input
              type="number"
              min={5}
              max={200}
              value={radius}
              onChange={(e) => {
                const v = Number(e.target.value)
                if (Number.isFinite(v) && v >= 5 && v <= 200) {
                  setQ({ radius: v === DEFAULT_MATCH_RADIUS_M ? null : Math.round(v) })
                }
              }}
            />{' '}
            m
          </label>
        </div>
      )}

      <SurveyMap
        markers={markers}
        gateways={gateways}
        tiles={tiles}
        pinMode={pinMode && !demo}
        onPinDrop={handlePinDrop}
        fitKey={`${query.node ?? ''}:${nodeProbes.length}`}
      />

      <div className="survey-legend" aria-label="Legend">
        <span className="survey-legend-title">
          {compareResult ? 'Δ dBm vs baseline (uplink)' : 'gateway RSSI, dBm (uplink)'}
        </span>
        {legendBands.map((b) => (
          <span key={b.label} className="chart-key">
            <span className="chart-key-swatch" style={{ background: b.color }} />
            {b.label}
          </span>
        ))}
        {compareResult && (
          <span className="chart-key">
            <span className="chart-key-swatch hollow" />
            no baseline here
          </span>
        )}
        {!tiles && (
          <span className="survey-private-note">
            private mode: no basemap requested — enable one in Settings
          </span>
        )}
      </div>

      <SurveyTimeline
        probes={nodeProbes}
        sessions={sessions}
        session={query.session}
        compare={query.compare}
        range={query.from && query.to ? { from: query.from, to: query.to } : null}
        onSelectSession={(index) => setQ({ session: index, from: null, to: null })}
        onBrush={(from, to) => setQ({ from, to, session: null })}
      />

      {showNoFix && visibleNoFix.length > 0 && (
        <div className="survey-no-fix-list">
          <div className="survey-no-fix-head">
            Probes without a GPS fix — the press happened, the position is fiction
            (either 0,0 or a stale last-known point), so they are never plotted:
          </div>
          {visibleNoFix.map((p) => (
            <div key={p.id} className="survey-no-fix-row">
              <span>#{p.seq ?? '—'}</span>
              <span>{formatEastern(p.received_at)}</span>
              <span>gw {formatDbm(p.gw_rssi)}</span>
              <span>
                {p.sats ?? 0} sats · hdop {p.hdop ?? '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
