import { Check, Images, Star } from 'lucide-react'
import { photoImageUrl } from '../api'
import { chartTimeLabel, timeAgo } from '../format'
import type { Sighting } from '../types'

interface SightingTileProps {
  sighting: Sighting
  selecting: boolean
  selected: boolean
  onClick: () => void
}

/** One burst as one tile: the cover frame with a photo-count pill. Follows
 *  PhotoTile's anatomy so the grouped and flat grids read as one product. */
export default function SightingTile({
  sighting,
  selecting,
  selected,
  onClick,
}: SightingTileProps) {
  const s = sighting
  const single = s.count === 1
  // "6:14 PM – 6:38 PM" for bursts (one label when the whole burst fits
  // inside a minute); singles read like PhotoTile.
  const startLabel = chartTimeLabel(Date.parse(s.started_at), 1)
  const endLabel = chartTimeLabel(Date.parse(s.ended_at), 1)
  const when = single
    ? timeAgo(s.cover.captured_at)
    : startLabel === endLabel
      ? startLabel
      : `${startLabel} – ${endLabel}`
  const label = single
    ? `Photo from ${s.camera_name}, ${when}`
    : `${s.count} photos from ${s.camera_name}, ${when}`

  return (
    <button
      type="button"
      className={`tile${selecting ? ' selectable' : ''}${selected ? ' selected' : ''}`}
      onClick={onClick}
      aria-label={selecting ? `${selected ? 'Deselect' : 'Select'} ${label}` : label}
      aria-pressed={selecting ? selected : undefined}
    >
      <img
        className="tile-img"
        src={photoImageUrl(s.cover.id, 'thumb', s.cover.thumb_size)}
        alt=""
        loading="lazy"
        draggable={false}
      />
      {selecting && (
        <span className={`tile-check${selected ? ' on' : ''}`} aria-hidden="true">
          <Check size={14} strokeWidth={3} />
        </span>
      )}
      <span className="tile-badges" aria-hidden="true">
        {!single && (
          <span className="tile-count" title={`${s.count} photos in this sighting`}>
            <Images size={13} aria-hidden="true" />
            {s.count}
          </span>
        )}
        {s.kept_count > 0 && (
          <span className="tile-star" title={single ? 'Saved forever' : `${s.kept_count} saved`}>
            <Star size={16} fill="currentColor" />
            {s.kept_count > 1 && <span className="tile-star-count">{s.kept_count}</span>}
          </span>
        )}
      </span>
      <span className="tile-caption">
        <span className="tile-camera">{s.camera_name}</span>
        <span className="tile-time">{when}</span>
      </span>
    </button>
  )
}
