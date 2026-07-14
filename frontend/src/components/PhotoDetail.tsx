import {
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock,
  Plus,
  RadioTower,
  Star,
  Tag as TagIcon,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, TouchEvent as ReactTouchEvent } from 'react'
import { ApiError, getFullRequest, photoImageUrl } from '../api'
import { expiryLabel, formatEastern, timeAgo } from '../format'
import { live } from '../live'
import type { FullRequest, MeshEvent, MeshTransfer, Photo, TagCount } from '../types'

interface PhotoDetailProps {
  photo: Photo
  /** Public read-only demo: hide keep/request/delete/tag-edit controls. */
  demo: boolean
  hasPrev: boolean
  hasNext: boolean
  onPrev: () => void
  onNext: () => void
  onClose: () => void
  onToggleKeep: () => Promise<void>
  onRequestFull: (quality: 'standard' | 'max') => Promise<void>
  onRefresh: () => Promise<void>
  onDelete: () => Promise<void>
  allTags: TagCount[]
  onAddTag: (name: string) => Promise<void>
  onRemoveTag: (slug: string) => Promise<void>
}

/** Species/content suggestions seeded into the tag datalist alongside real tags. */
const PRESET_TAGS = [
  'Deer',
  'Buck',
  'Doe',
  'Fawn',
  'Turkey',
  'Coyote',
  'Fox',
  'Raccoon',
  'Squirrel',
  'Rabbit',
  'Bird',
  'Bear',
  'Person',
  'Vehicle',
  'Empty',
]

const SWIPE_MIN_PX = 48
/** Re-check an in-flight photo-pull request this often while the overlay is open. */
const REQUEST_POLL_MS = 15_000

/** What each wire quality means to a human: standard = the quick downscaled
 * HD frame; max = the camera's untouched sensor file. */
const QUALITY_LABEL: Record<string, string> = { standard: 'HD', max: 'Original' }
/** Honest airtime expectations, from the tier byte budgets at LoRa goodput. */
const QUALITY_ETA: Record<string, string> = { standard: '3–5 minutes', max: '10–20 minutes' }

function kb(bytes: number | null): string {
  return bytes == null ? '' : ` · ${Math.max(1, Math.round(bytes / 1024))} KB`
}

function minutes(seconds: number): string {
  if (seconds < 50) return 'under a minute'
  const m = Math.round(seconds / 60)
  return m <= 1 ? 'about a minute' : `about ${m} minutes`
}

function elapsedLabel(sinceIso: string, now: number): string {
  const s = Math.max(0, Math.floor((now - new Date(sinceIso).getTime()) / 1000))
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)}m ${(s % 60).toString().padStart(2, '0')}s`
}

export default function PhotoDetail({
  photo,
  demo,
  hasPrev,
  hasNext,
  onPrev,
  onNext,
  onClose,
  onToggleKeep,
  onRequestFull,
  onRefresh,
  onDelete,
  allTags,
  onAddTag,
  onRemoveTag,
}: PhotoDetailProps) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [confirmingMax, setConfirmingMax] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [addingTag, setAddingTag] = useState(false)
  const [tagDraft, setTagDraft] = useState('')
  const [fullReq, setFullReq] = useState<FullRequest | null>(null)
  // Latest live transfer beat for THIS photo (from the shared mesh SSE stream):
  // drives the progress bar while a pull is crossing the radio.
  const [xfer, setXfer] = useState<MeshTransfer | null>(null)
  // 1 Hz tick while a request is outstanding, so elapsed/ETA text stays alive.
  const [nowTick, setNowTick] = useState(() => Date.now())
  const [imgDims, setImgDims] = useState<string | null>(null)
  const touchStart = useRef<{ x: number; y: number } | null>(null)
  const onRefreshRef = useRef(onRefresh)
  onRefreshRef.current = onRefresh

  // Reset transient state when navigating between photos.
  useEffect(() => {
    setConfirmingDelete(false)
    setConfirmingMax(false)
    setActionError(null)
    setImgDims(null)
    setAddingTag(false)
    setTagDraft('')
  }, [photo.id])

  // Live request diagnostics via the tab's shared SSE stream: 'request' events
  // for this photo update the panel instantly (photo upgrades themselves arrive
  // through App's 'photo' subscription and flow back in as props). One initial
  // fetch seeds the panel; a 15 s poll engages only while the stream is down.
  useEffect(() => {
    setFullReq(null)
    let cancelled = false
    let timer: number | undefined

    void getFullRequest(photo.id)
      .then((req) => {
        if (!cancelled) setFullReq(req)
      })
      .catch(() => {})

    const offRequest = live.subscribe('request', (d) => {
      const ev = d as { photo_id: string; request: FullRequest }
      if (!cancelled && ev.photo_id === photo.id) setFullReq(ev.request)
    })

    const poll = async () => {
      try {
        const req = await getFullRequest(photo.id)
        if (cancelled) return
        setFullReq(req)
        if (req?.status === 'done') void onRefreshRef.current()
      } catch {
        // best-effort
      }
      if (!cancelled && !live.connected) {
        timer = window.setTimeout(() => void poll(), REQUEST_POLL_MS)
      }
    }
    const offStatus = live.onStatus((up) => {
      if (!up && !cancelled && timer === undefined) {
        timer = window.setTimeout(() => void poll(), REQUEST_POLL_MS)
      } else if (up && timer !== undefined) {
        window.clearTimeout(timer)
        timer = undefined
      }
    })

    return () => {
      cancelled = true
      offRequest()
      offStatus()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [photo.id])

  const requestOutstanding =
    photo.full_requested ||
    fullReq?.status === 'pending' ||
    fullReq?.status === 'delivered'

  // Live transfer progress: every chunk the gateway pulls is beaconed on the
  // mesh stream; keep the latest beat that belongs to this photo.
  useEffect(() => {
    setXfer(null)
    return live.subscribe('mesh', (d) => {
      const t = (d as MeshEvent).extra?.transfer
      if (t && t.event_id === photo.event_id) setXfer(t)
    })
  }, [photo.event_id])

  useEffect(() => {
    if (!requestOutstanding) return
    setNowTick(Date.now())
    const id = window.setInterval(() => setNowTick(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [requestOutstanding])

  // Keyboard: Escape closes, arrows navigate.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'ArrowLeft' && hasPrev) {
        e.preventDefault()
        onPrev()
      } else if (e.key === 'ArrowRight' && hasNext) {
        e.preventDefault()
        onNext()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onPrev, onNext, hasPrev, hasNext])

  // Lock body scroll while the overlay is open.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  const closeOnSelf = (e: ReactMouseEvent) => {
    if (e.target === e.currentTarget) onClose()
  }

  const handleTouchStart = (e: ReactTouchEvent) => {
    const touch = e.touches.item(0)
    touchStart.current = touch ? { x: touch.clientX, y: touch.clientY } : null
  }

  const handleTouchEnd = (e: ReactTouchEvent) => {
    const start = touchStart.current
    touchStart.current = null
    if (!start) return
    const touch = e.changedTouches.item(0)
    if (!touch) return
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < Math.abs(dy) * 1.5) return
    if (dx < 0) {
      if (hasNext) onNext()
    } else if (hasPrev) {
      onPrev()
    }
  }

  const runAction = async (action: () => Promise<void>, failure: string) => {
    if (busy) return
    setBusy(true)
    setActionError(null)
    try {
      await action()
    } catch (err) {
      // 429 = the demo's shared mesh queue is full; its detail explains itself
      setActionError(err instanceof ApiError && err.status === 429 ? err.message : failure)
    } finally {
      setBusy(false)
    }
  }

  // Requests work in the demo too — they queue a real command that the
  // simulated mesh delivers at LoRa speed (that's the whole pitch).
  // Wire values stay standard/max; the UI says HD/original (see QUALITY_LABEL).
  const handleRequestFull = (quality: 'standard' | 'max') => {
    void runAction(async () => {
      await onRequestFull(quality)
      setConfirmingMax(false)
    }, quality === 'max' ? 'Could not request the original' : 'Could not request HD')
  }

  const meta = photo.meta ?? {}
  const facts: { label: string; value: string }[] = []
  if (meta.battery_v != null) {
    facts.push({ label: 'Battery', value: `${meta.battery_v.toFixed(2)} V` })
  }
  if (meta.rssi != null || meta.snr != null) {
    const rssi = meta.rssi != null ? `${meta.rssi} dBm` : ''
    const snr = meta.snr != null ? `${meta.snr} dB SNR` : ''
    facts.push({ label: 'Radio', value: [rssi, snr].filter(Boolean).join(' / ') })
  }
  if (meta.temp_c != null) {
    facts.push({ label: 'Temp', value: `${meta.temp_c.toFixed(1)} °C` })
  }

  // Transfer progress derived from the latest mesh beat for this photo.
  const reassembling = requestOutstanding && xfer?.reassembled != null
  const transferring =
    requestOutstanding && !reassembling && xfer != null && xfer.chunk != null
  const transferredBytes = transferring ? (xfer.offset ?? 0) + (xfer.bytes ?? 0) : 0
  const progressPct = reassembling
    ? 100
    : transferring && xfer?.total
      ? Math.min(99, Math.round((transferredBytes / xfer.total) * 100))
      : null
  const etaSeconds =
    transferring && xfer?.total && xfer.bps
      ? Math.max(0, (xfer.total - transferredBytes) / xfer.bps)
      : null

  return (
    <div
      className="detail-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={`Photo from ${photo.camera_name}`}
      onClick={closeOnSelf}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      <div className="detail-topbar">
        <span className="detail-title">{photo.camera_name}</span>
        <button
          type="button"
          className="btn icon-btn detail-close"
          aria-label="Close"
          onClick={onClose}
        >
          <X size={20} aria-hidden="true" />
        </button>
      </div>

      <div className="detail-stage" onClick={closeOnSelf}>
        {hasPrev && (
          <button
            type="button"
            className="nav-arrow nav-prev"
            aria-label="Previous photo"
            onClick={onPrev}
          >
            <ChevronLeft size={32} aria-hidden="true" />
          </button>
        )}
        <img
          key={`${photo.id}-${photo.has_full ? `full-${photo.full_size ?? 0}` : 'thumb'}`}
          className="detail-img"
          src={photoImageUrl(
            photo.id,
            photo.has_full ? 'full' : 'thumb',
            photo.has_full ? photo.full_size : photo.thumb_size,
          )}
          alt={`Trail camera photo from ${photo.camera_name}`}
          draggable={false}
          onLoad={(e) => {
            const el = e.currentTarget
            setImgDims(`${el.naturalWidth}×${el.naturalHeight}`)
          }}
        />
        {hasNext && (
          <button
            type="button"
            className="nav-arrow nav-next"
            aria-label="Next photo"
            onClick={onNext}
          >
            <ChevronRight size={32} aria-hidden="true" />
          </button>
        )}
      </div>

      <div className="detail-info">
        <div className="detail-meta">
          <span className="detail-meta-item detail-camera">{photo.camera_name}</span>
          <span className="detail-meta-item detail-site">{photo.site_slug}</span>
          <span className="detail-meta-item">{formatEastern(photo.captured_at)}</span>
          {facts.map((fact) => (
            <span key={fact.label} className="detail-meta-item detail-fact">
              <span className="detail-fact-label">{fact.label}</span> {fact.value}
            </span>
          ))}
        </div>

        <div className="detail-tags">
          {photo.tags.map((t) => (
            <span key={t.slug} className="tag-chip">
              <TagIcon size={12} aria-hidden="true" />
              {t.name}
              {!demo && (
                <button
                  type="button"
                  className="tag-remove"
                  aria-label={`Remove tag ${t.name}`}
                  disabled={busy}
                  onClick={() =>
                    void runAction(() => onRemoveTag(t.slug), 'Could not remove tag')
                  }
                >
                  <X size={12} aria-hidden="true" />
                </button>
              )}
            </span>
          ))}
          {demo ? null : addingTag ? (
            <form
              className="tag-add-form"
              onSubmit={(e) => {
                e.preventDefault()
                const name = tagDraft.trim()
                if (!name) return
                void runAction(async () => {
                  await onAddTag(name)
                  setTagDraft('')
                  setAddingTag(false)
                }, 'Could not add tag')
              }}
            >
              <input
                className="tag-input"
                list="tag-suggestions"
                placeholder="deer, buck, turkey…"
                value={tagDraft}
                autoFocus
                aria-label="New tag"
                onChange={(e) => setTagDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    setAddingTag(false)
                    setTagDraft('')
                  }
                }}
              />
              <datalist id="tag-suggestions">
                {[
                  ...new Set([...allTags.map((t) => t.name), ...PRESET_TAGS]),
                ].map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              <button type="submit" className="btn" disabled={busy || !tagDraft.trim()}>
                Add
              </button>
              <button
                type="button"
                className="btn icon-btn"
                aria-label="Cancel adding tag"
                onClick={() => {
                  setAddingTag(false)
                  setTagDraft('')
                }}
              >
                <X size={14} aria-hidden="true" />
              </button>
            </form>
          ) : (
            <button
              type="button"
              className="tag-chip tag-add"
              disabled={busy}
              onClick={() => setAddingTag(true)}
            >
              <Plus size={12} aria-hidden="true" />
              Tag
            </button>
          )}
        </div>

        <div className={`detail-expiry${photo.keep ? ' kept' : ''}`}>
          {expiryLabel(photo.expires_at, photo.keep)}
          <span className={`detail-viewing${photo.has_full ? ' full' : ''}`}>
            {photo.has_full
              ? `Viewing ${
                  fullReq?.status === 'done' && fullReq.quality === 'max' ? 'original' : 'HD'
                }${imgDims ? ` ${imgDims}` : ''}${kb(photo.full_size)}`
              : `Viewing thumbnail${imgDims ? ` ${imgDims}` : ''}${kb(photo.thumb_size)}`}
          </span>
        </div>

        {fullReq && (requestOutstanding || !photo.has_full) && (
          <div className="hd-diag" aria-live="polite">
            <div className="hd-diag-title">
              <RadioTower size={14} aria-hidden="true" />
              {QUALITY_LABEL[fullReq.quality] ?? 'Photo'} request
              <span className={`hd-diag-badge ${fullReq.status}`}>{fullReq.status}</span>
            </div>
            <ol className="hd-diag-steps">
              <li className="step-done">
                <Check size={13} aria-hidden="true" />
                Queued {formatEastern(fullReq.created_at)}
                {fullReq.requested_by ? ` by ${fullReq.requested_by}` : ''}
              </li>
              <li className={fullReq.delivered_at ? 'step-done' : 'step-wait'}>
                {fullReq.delivered_at ? (
                  <>
                    <Check size={13} aria-hidden="true" />
                    Gateway picked it up {formatEastern(fullReq.delivered_at)}
                  </>
                ) : (
                  <>
                    <Clock size={13} aria-hidden="true" />
                    Waiting for the gateway to poll (every ~30 s while it&apos;s running)
                  </>
                )}
              </li>
              {fullReq.status === 'delivered' && !transferring && !reassembling && (
                <li className="step-wait">
                  <Clock size={13} aria-hidden="true" />
                  Relaying to the node; {photo.camera_name} last heard{' '}
                  {fullReq.node_last_seen_at ? timeAgo(fullReq.node_last_seen_at) : 'never'}
                  {' '}(sleepy nodes answer on their next wake)
                </li>
              )}
              {(transferring || reassembling) && (
                <li className="step-wait">
                  <Clock size={13} aria-hidden="true" />
                  {reassembling
                    ? 'All chunks received, reassembling and uploading'
                    : `Transferring over the mesh: chunk ${xfer?.chunk} of ${xfer?.chunks}`}
                </li>
              )}
              {fullReq.status === 'failed' && (
                <li className="step-fail">
                  <CircleAlert size={13} aria-hidden="true" />
                  Failed{fullReq.detail ? ` (${fullReq.detail})` : ''}. You can request again
                </li>
              )}
              {fullReq.status === 'expired' && (
                <li className="step-fail">
                  <CircleAlert size={13} aria-hidden="true" />
                  Expired unanswered after 14 days. You can request again
                </li>
              )}
            </ol>
            {requestOutstanding && (
              <div className="hd-progress">
                <div
                  className={`hd-progress-track${transferring || reassembling ? '' : ' indeterminate'}`}
                  role="progressbar"
                  aria-valuenow={progressPct ?? undefined}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    className="hd-progress-fill"
                    style={progressPct != null ? { width: `${progressPct}%` } : undefined}
                  />
                </div>
                <div className="hd-progress-note">
                  {reassembling
                    ? 'Almost there: the gateway is stitching the chunks together.'
                    : transferring && xfer?.total
                      ? `${Math.round(transferredBytes / 1024)} of ${Math.round(xfer.total / 1024)} KB` +
                        (etaSeconds != null ? ` · ${minutes(etaSeconds)} to go` : '')
                      : `Still working: ${QUALITY_LABEL[fullReq.quality] ?? 'this'} pulls over the LoRa mesh ` +
                        `usually take ${QUALITY_ETA[fullReq.quality] ?? 'a few minutes'}. ` +
                        `Elapsed ${elapsedLabel(fullReq.created_at, nowTick)}.`}
                </div>
              </div>
            )}
          </div>
        )}

        {actionError && <div className="detail-error">{actionError}</div>}

        <div className="detail-actions">
          {!demo && (
          <button
            type="button"
            className={`btn keep-btn${photo.keep ? ' kept' : ''}`}
            disabled={busy}
            aria-pressed={photo.keep}
            onClick={() => void runAction(onToggleKeep, 'Could not update photo')}
          >
            <Star
              size={16}
              fill={photo.keep ? 'currentColor' : 'none'}
              aria-hidden="true"
            />
            {photo.keep ? 'Saved' : 'Save forever'}
          </button>
          )}

          {requestOutstanding ? (
            <button type="button" className="btn" disabled>
              <RadioTower size={16} aria-hidden="true" />
              {fullReq?.quality === 'max' ? 'Original requested' : 'HD requested'}
            </button>
          ) : confirmingMax ? (
            <div className="confirm-group">
              <span className="confirm-text">
                The original is the camera&apos;s untouched sensor file (2048×1536).
                Pulling it over the mesh takes {QUALITY_ETA.max} of radio airtime and
                real battery on the node. HD is usually enough.
              </span>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => handleRequestFull('max')}
              >
                <RadioTower size={16} aria-hidden="true" />
                Pull the original
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirmingMax(false)}
              >
                Cancel
              </button>
            </div>
          ) : (
            <>
              {!photo.has_full && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  title={`A sharper downscaled frame, usually ${QUALITY_ETA.standard} over the mesh`}
                  onClick={() => handleRequestFull('standard')}
                >
                  <RadioTower size={16} aria-hidden="true" />
                  Request HD
                </button>
              )}
              <button
                type="button"
                className="btn"
                disabled={busy}
                title={`The camera's untouched sensor file, ${QUALITY_ETA.max} over the mesh`}
                onClick={() => setConfirmingMax(true)}
              >
                <RadioTower size={16} aria-hidden="true" />
                Original…
              </button>
            </>
          )}

          {demo ? null : confirmingDelete ? (
            <div className="confirm-group">
              <span className="confirm-text">Delete this photo?</span>
              <button
                type="button"
                className="btn danger-btn"
                disabled={busy}
                onClick={() => void runAction(onDelete, 'Could not delete photo')}
              >
                <Trash2 size={16} aria-hidden="true" />
                Delete
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirmingDelete(false)}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn danger-ghost-btn"
              disabled={busy}
              onClick={() => setConfirmingDelete(true)}
            >
              <Trash2 size={16} aria-hidden="true" />
              Delete
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
