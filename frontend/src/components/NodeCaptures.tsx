import { ArrowRight, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import * as api from '../api'
import { UnauthorizedError } from '../api'
import { formatEastern, timeAgo } from '../format'
import { feedUrl, navigate, photoUrl } from '../router'
import type { FeedFilters } from '../router'
import type { NodeHealth, Photo } from '../types'

interface NodeCapturesProps {
  node: NodeHealth
  onUnauthorized: () => void
}

const STRIP_LIMIT = 12

/** In-app link: a real href so middle/ctrl/shift-click open a tab like any
 *  normal link; a plain left click stays a client-side navigation. */
function navClick(e: ReactMouseEvent<HTMLAnchorElement>) {
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  e.preventDefault()
  navigate(e.currentTarget.getAttribute('href') ?? '/')
}

/** The latest thumbnails from one camera — the "which camera is this" anchor
 *  at the top of the node detail. A tap expands the photo in a lightbox right
 *  here; the caption links out to the feed, which keeps owning pagination,
 *  stars and full-res requests. */
export default function NodeCaptures({ node, onUnauthorized }: NodeCapturesProps) {
  const [photos, setPhotos] = useState<Photo[] | null>(null)
  const [error, setError] = useState(false)
  const [viewIdx, setViewIdx] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setPhotos(null)
    setError(false)
    setViewIdx(null)
    api
      .getPhotos({ cameraId: node.id, limit: STRIP_LIMIT })
      .then((page) => {
        if (!cancelled) setPhotos(page.items)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof UnauthorizedError) {
          onUnauthorized()
          return
        }
        setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [node.id, onUnauthorized])

  const count = photos?.length ?? 0
  const viewed = viewIdx !== null && photos !== null ? photos[viewIdx] : null

  const step = useCallback(
    (delta: number) => {
      setViewIdx((idx) => {
        if (idx === null || count === 0) return idx
        return (idx + delta + count) % count
      })
    },
    [count],
  )

  // Capture-phase so the lightbox owns Escape/arrows while open — NodeDetail's
  // own window listener would otherwise close the whole node overlay.
  const open = viewed !== null
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setViewIdx(null)
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        step(-1)
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        step(1)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, step])

  const cameraFeed: FeedFilters = {
    site: null,
    cameraId: node.id,
    keptOnly: false,
    date: null,
    tag: null,
    flat: false,
    from: null,
    to: null,
    at: null,
  }

  const closeOnSelf = (e: ReactMouseEvent) => {
    if (e.target === e.currentTarget) setViewIdx(null)
  }

  if (error) return null
  if (photos !== null && photos.length === 0) {
    return <div className="captures-empty">No captures from this camera yet.</div>
  }

  return (
    <section className="node-captures" aria-label="Recent captures">
      <div className="captures-head">
        <span className="captures-title">Recent captures</span>
        <a className="captures-all" href={feedUrl(cameraFeed)} onClick={navClick}>
          View all
          <ArrowRight size={13} aria-hidden="true" />
        </a>
      </div>
      <div className="captures-strip">
        {photos === null
          ? Array.from({ length: 4 }, (_, i) => (
              <span className="captures-thumb placeholder" key={i} aria-hidden="true" />
            ))
          : photos.map((photo, i) => (
              <button
                type="button"
                className="captures-thumb"
                key={photo.id}
                aria-label={`View photo captured ${timeAgo(photo.captured_at)}`}
                title={timeAgo(photo.captured_at)}
                onClick={() => setViewIdx(i)}
              >
                <img
                  src={api.photoImageUrl(photo.id, 'thumb', photo.thumb_size)}
                  alt=""
                  loading="lazy"
                  draggable={false}
                />
              </button>
            ))}
      </div>

      {viewed && (
        <div
          className="captures-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`Photo captured ${timeAgo(viewed.captured_at)}`}
          onClick={closeOnSelf}
        >
          <button
            type="button"
            className="btn icon-btn captures-lightbox-close"
            aria-label="Close photo"
            onClick={() => setViewIdx(null)}
          >
            <X size={20} aria-hidden="true" />
          </button>
          <div className="captures-lightbox-stage" onClick={closeOnSelf}>
            {count > 1 && (
              <button
                type="button"
                className="btn icon-btn captures-nav"
                aria-label="Previous photo"
                onClick={() => step(-1)}
              >
                <ChevronLeft size={22} aria-hidden="true" />
              </button>
            )}
            <img
              // The mesh delivers a small thumb first; full-res only exists
              // once someone requested it over LoRa. Show the best we have.
              src={api.photoImageUrl(
                viewed.id,
                viewed.has_full ? 'full' : 'thumb',
                viewed.has_full ? viewed.full_size : viewed.thumb_size,
              )}
              alt={`Photo from ${viewed.camera_name}`}
              draggable={false}
            />
            {count > 1 && (
              <button
                type="button"
                className="btn icon-btn captures-nav"
                aria-label="Next photo"
                onClick={() => step(1)}
              >
                <ChevronRight size={22} aria-hidden="true" />
              </button>
            )}
          </div>
          <div className="captures-lightbox-caption">
            <span className="captures-lightbox-time">
              {formatEastern(viewed.captured_at)}
              {count > 1 && (
                <span className="captures-lightbox-count">
                  {' '}
                  · {(viewIdx ?? 0) + 1} / {count}
                </span>
              )}
            </span>
            <a
              className="captures-all"
              href={photoUrl(viewed.id, cameraFeed)}
              onClick={navClick}
            >
              Open in gallery
              <ArrowRight size={13} aria-hidden="true" />
            </a>
          </div>
        </div>
      )}
    </section>
  )
}
