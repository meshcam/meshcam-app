import { Check, Star } from 'lucide-react'
import { photoImageUrl } from '../api'
import { timeAgo } from '../format'
import type { Photo } from '../types'

interface PhotoTileProps {
  photo: Photo
  selecting: boolean
  selected: boolean
  onClick: () => void
}

export default function PhotoTile({ photo, selecting, selected, onClick }: PhotoTileProps) {
  const ago = timeAgo(photo.captured_at)
  return (
    <button
      type="button"
      className={`tile${selecting ? ' selectable' : ''}${selected ? ' selected' : ''}`}
      onClick={onClick}
      aria-label={
        selecting
          ? `${selected ? 'Deselect' : 'Select'} photo from ${photo.camera_name}, ${ago}`
          : `Photo from ${photo.camera_name}, ${ago}`
      }
      aria-pressed={selecting ? selected : undefined}
    >
      <img
        className="tile-img"
        src={photoImageUrl(photo.id, 'thumb', photo.thumb_size)}
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
        {photo.has_full ? (
          <span className="tile-hd" title="HD available">
            HD
          </span>
        ) : photo.full_requested ? (
          <span className="tile-hd pending" title="Quality requested — crossing the mesh">
            HD…
          </span>
        ) : null}
        {photo.keep && (
          <span className="tile-star" title="Saved forever">
            <Star size={16} fill="currentColor" />
          </span>
        )}
      </span>
      <span className="tile-caption">
        <span className="tile-camera">{photo.camera_name}</span>
        <span className="tile-time">{ago}</span>
      </span>
    </button>
  )
}
