import { ImageOff } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { dayLabel, easternDayKey } from '../format'
import type { Photo } from '../types'
import PhotoTile from './PhotoTile'

interface PhotoGridProps {
  photos: Photo[]
  loading: boolean
  hasMore: boolean
  error: string | null
  onLoadMore: () => void
  onRetry: () => void
  onSelect: (id: string) => void
  selecting: boolean
  selectedIds: ReadonlySet<string>
  onToggleSelect: (id: string) => void
}

interface DayGroup {
  label: string
  photos: Photo[]
}

export default function PhotoGrid({
  photos,
  loading,
  hasMore,
  error,
  onLoadMore,
  onRetry,
  onSelect,
  selecting,
  selectedIds,
  onToggleSelect,
}: PhotoGridProps) {
  const sentinelRef = useRef<HTMLDivElement>(null)

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

  // Consecutive-run grouping by Eastern capture day. The feed is ordered by
  // arrival, so a store-and-forward straggler can repeat a day header — that's
  // honest ("these came in later") and keeps the feed order stable.
  const groups = useMemo<DayGroup[]>(() => {
    const out: DayGroup[] = []
    let lastKey: string | null = null
    for (const photo of photos) {
      const key = easternDayKey(photo.captured_at)
      if (key === lastKey && out.length > 0) {
        out[out.length - 1].photos.push(photo)
      } else {
        out.push({ label: dayLabel(photo.captured_at), photos: [photo] })
        lastKey = key
      }
    }
    return out
  }, [photos])

  const isEmpty = !loading && !error && photos.length === 0 && !hasMore

  return (
    <main className="grid-wrap">
      {groups.map((group, i) => (
        <section key={`${group.label}-${i}`} aria-label={group.label}>
          <h2 className="day-heading">{group.label}</h2>
          <div className="photo-grid">
            {group.photos.map((photo) => (
              <PhotoTile
                key={photo.id}
                photo={photo}
                selecting={selecting}
                selected={selectedIds.has(photo.id)}
                onClick={() =>
                  selecting ? onToggleSelect(photo.id) : onSelect(photo.id)
                }
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
