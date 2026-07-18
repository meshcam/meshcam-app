import { ImageOff } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { dayLabel, easternDayKey } from '../format'
import { usePrependCompensation } from '../scroll'
import type { Sighting } from '../types'
import SightingTile from './SightingTile'

interface SightingGridProps {
  sightings: Sighting[]
  loading: boolean
  hasMore: boolean
  /** Anchored feed: newer items exist above what's loaded (time-scrubber jump). */
  hasMoreUp: boolean
  loadingUp: boolean
  error: string | null
  onLoadMore: () => void
  onLoadMoreUp: () => void
  onRetry: () => void
  onSelect: (sighting: Sighting) => void
  selecting: boolean
  selectedIds: ReadonlySet<string>
  onToggleSelect: (sighting: Sighting) => void
}

interface DayGroup {
  day: string
  label: string
  sightings: Sighting[]
}

/** The grouped feed — PhotoGrid's shape (day headings by consecutive run,
 *  sentinel-driven infinite scroll) over sighting tiles. Kept separate from
 *  PhotoGrid on purpose: the flat feed stays untouched for ?flat=1. */
export default function SightingGrid({
  sightings,
  loading,
  hasMore,
  hasMoreUp,
  loadingUp,
  error,
  onLoadMore,
  onLoadMoreUp,
  onRetry,
  onSelect,
  selecting,
  selectedIds,
  onToggleSelect,
}: SightingGridProps) {
  const sentinelRef = useRef<HTMLDivElement>(null)
  const topSentinelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = sentinelRef.current
    if (!el || !hasMore) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onLoadMore()
      },
      { rootMargin: '800px 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasMore, onLoadMore])

  useEffect(() => {
    const el = topSentinelRef.current
    if (!el || !hasMoreUp) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onLoadMoreUp()
      },
      { rootMargin: '600px 0px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasMoreUp, onLoadMoreUp])

  usePrependCompensation(
    useMemo(() => sightings.map((s) => s.id), [sightings]),
    hasMoreUp,
  )

  // Consecutive-run grouping by the sighting's first-frame Eastern day; the
  // feed is arrival-ordered, so a straggler-resurfaced sighting can repeat a
  // day header — honest, same as PhotoGrid.
  const groups = useMemo<DayGroup[]>(() => {
    const out: DayGroup[] = []
    let lastKey: string | null = null
    for (const s of sightings) {
      const key = easternDayKey(s.started_at)
      if (key === lastKey && out.length > 0) {
        out[out.length - 1].sightings.push(s)
      } else {
        out.push({ day: key, label: dayLabel(s.started_at), sightings: [s] })
        lastKey = key
      }
    }
    return out
  }, [sightings])

  const isEmpty = !loading && !error && sightings.length === 0 && !hasMore && !hasMoreUp

  return (
    <main className="grid-wrap">
      {hasMoreUp && sightings.length > 0 && (
        <div ref={topSentinelRef} className="grid-sentinel" aria-hidden="true" />
      )}
      {loadingUp && (
        <div className="grid-status" role="status">
          <span className="spinner" />
          <span>Loading newer…</span>
        </div>
      )}
      {/* Keyed by first-member id, not index — see PhotoGrid: index keys make
          prepends remount every section. */}
      {groups.map((group) => (
        <section key={group.sightings[0].id} aria-label={group.label} data-day={group.day}>
          <h2 className="day-heading">{group.label}</h2>
          <div className="photo-grid">
            {group.sightings.map((s) => (
              <SightingTile
                key={s.id}
                sighting={s}
                selecting={selecting}
                selected={selectedIds.has(s.id)}
                onClick={() => (selecting ? onToggleSelect(s) : onSelect(s))}
              />
            ))}
          </div>
        </section>
      ))}

      {loading && (
        <div className="grid-status" role="status">
          <span className="spinner" />
          <span>Loading photos…</span>
        </div>
      )}

      {error && !loading && (
        <div className="grid-status grid-error" role="alert">
          <span>{error}</span>
          <button type="button" className="btn" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}

      {isEmpty && (
        <div className="empty-state">
          <div className="empty-emblem" aria-hidden="true">
            <ImageOff size={48} />
          </div>
          <p className="empty-title">No photos yet</p>
          <p className="empty-hint">
            Photos from the mesh will show up here. Try a different site, camera, date, or
            turn off the Saved filter.
          </p>
        </div>
      )}

      <div ref={sentinelRef} className="grid-sentinel" aria-hidden="true" />
    </main>
  )
}
