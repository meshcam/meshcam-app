import { Star, Tag as TagIcon, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { TagCount } from '../types'

interface BulkBarProps {
  count: number
  busy: boolean
  tags: TagCount[]
  onSave: () => void
  onTag: (name: string) => void
  onDelete: () => void
  onCancel: () => void
}

/** Fixed action bar shown while multi-selecting photos in the feed. */
export default function BulkBar({
  count,
  busy,
  tags,
  onSave,
  onTag,
  onDelete,
  onCancel,
}: BulkBarProps) {
  // Two-step delete; disarm whenever the selection changes under it.
  const [armed, setArmed] = useState(false)
  const [tagging, setTagging] = useState(false)
  const [tagDraft, setTagDraft] = useState('')
  useEffect(() => {
    setArmed(false)
  }, [count])

  if (tagging) {
    return (
      <form
        className="bulk-bar"
        role="toolbar"
        aria-label="Bulk tag"
        onSubmit={(e) => {
          e.preventDefault()
          const name = tagDraft.trim()
          if (!name) return
          onTag(name)
          setTagging(false)
          setTagDraft('')
        }}
      >
        <span className="bulk-count">Tag {count} photo{count === 1 ? '' : 's'}:</span>
        <input
          className="tag-input"
          list="bulk-tag-suggestions"
          placeholder="deer, buck, turkey…"
          value={tagDraft}
          autoFocus
          disabled={busy}
          aria-label="Tag name"
          onChange={(e) => setTagDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setTagging(false)
          }}
        />
        <datalist id="bulk-tag-suggestions">
          {tags.map((t) => (
            <option key={t.slug} value={t.name} />
          ))}
        </datalist>
        <button type="submit" className="btn" disabled={busy || !tagDraft.trim()}>
          Apply
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() => setTagging(false)}
        >
          Cancel
        </button>
      </form>
    )
  }

  return (
    <div className="bulk-bar" role="toolbar" aria-label="Bulk actions">
      <span className="bulk-count">
        {count === 0 ? 'Tap photos to select' : `${count} selected`}
      </span>
      <span className="bulk-spacer" />
      <button
        type="button"
        className="btn"
        disabled={busy || count === 0}
        onClick={onSave}
      >
        <Star size={16} aria-hidden="true" />
        Save
      </button>
      <button
        type="button"
        className="btn"
        disabled={busy || count === 0}
        onClick={() => setTagging(true)}
      >
        <TagIcon size={16} aria-hidden="true" />
        Tag
      </button>
      {armed ? (
        <button
          type="button"
          className="btn danger-btn"
          disabled={busy}
          onClick={onDelete}
        >
          <Trash2 size={16} aria-hidden="true" />
          Delete {count} photo{count === 1 ? '' : 's'}?
        </button>
      ) : (
        <button
          type="button"
          className="btn danger-ghost-btn"
          disabled={busy || count === 0}
          onClick={() => setArmed(true)}
        >
          <Trash2 size={16} aria-hidden="true" />
          Delete
        </button>
      )}
      <button type="button" className="btn" disabled={busy} onClick={onCancel}>
        <X size={16} aria-hidden="true" />
        Done
      </button>
    </div>
  )
}
