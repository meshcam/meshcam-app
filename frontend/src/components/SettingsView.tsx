import {
  Check,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Pencil,
  Plus,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import { UnauthorizedError } from '../api'
import { formatEastern, timeAgo } from '../format'
import type { Camera, DeviceToken, DeviceTokenCreated, RetentionStats, Site } from '../types'

interface SettingsViewProps {
  /** Public read-only demo: hide rename/hide/token/notes write controls. */
  demo: boolean
  onUnauthorized: () => void
  /** Fired after any site/camera change so the app refetches its filter lists. */
  onCatalogChange: () => void
}

/** Inline rename: text + pencil, or input + save/cancel while editing.
 *  In read-only demo mode it collapses to plain text (no pencil). */
function EditableName({
  value,
  label,
  readOnly = false,
  onSave,
}: {
  value: string
  label: string
  readOnly?: boolean
  onSave: (next: string) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [busy, setBusy] = useState(false)

  const commit = async () => {
    const next = draft.trim()
    if (!next || next === value) {
      setEditing(false)
      setDraft(value)
      return
    }
    setBusy(true)
    try {
      await onSave(next)
      setEditing(false)
    } finally {
      setBusy(false)
    }
  }

  if (readOnly) {
    return (
      <span className="editable-name">
        <span className="editable-value">{value}</span>
      </span>
    )
  }
  if (!editing) {
    return (
      <span className="editable-name">
        <span className="editable-value">{value}</span>
        <button
          type="button"
          className="icon-btn"
          aria-label={`Rename ${label}`}
          onClick={() => {
            setDraft(value)
            setEditing(true)
          }}
        >
          <Pencil size={14} aria-hidden="true" />
        </button>
      </span>
    )
  }
  return (
    <span className="editable-name editing">
      <input
        className="settings-input"
        value={draft}
        disabled={busy}
        autoFocus
        aria-label={`New name for ${label}`}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void commit()
          if (e.key === 'Escape') {
            setEditing(false)
            setDraft(value)
          }
        }}
      />
      <button
        type="button"
        className="icon-btn"
        aria-label="Save name"
        disabled={busy}
        onClick={() => void commit()}
      >
        <Check size={14} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="icon-btn"
        aria-label="Cancel rename"
        disabled={busy}
        onClick={() => {
          setEditing(false)
          setDraft(value)
        }}
      >
        <X size={14} aria-hidden="true" />
      </button>
    </span>
  )
}

function HideToggle({
  hidden,
  label,
  onToggle,
}: {
  hidden: boolean
  label: string
  onToggle: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      type="button"
      className={`btn hide-toggle${hidden ? ' hidden-on' : ''}`}
      aria-pressed={hidden}
      aria-label={hidden ? `Unhide ${label}` : `Hide ${label} from filters`}
      disabled={busy}
      onClick={() => {
        setBusy(true)
        void onToggle().finally(() => setBusy(false))
      }}
    >
      {hidden ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
      {hidden ? 'Hidden' : 'Visible'}
    </button>
  )
}

/** Two-step revoke — first click arms, second click within the row confirms. */
function RevokeButton({ name, onRevoke }: { name: string; onRevoke: () => Promise<void> }) {
  const [armed, setArmed] = useState(false)
  const [busy, setBusy] = useState(false)
  if (!armed) {
    return (
      <button
        type="button"
        className="btn"
        aria-label={`Revoke token ${name}`}
        onClick={() => setArmed(true)}
      >
        <Trash2 size={14} aria-hidden="true" />
        Revoke
      </button>
    )
  }
  return (
    <span className="revoke-confirm">
      <button
        type="button"
        className="btn danger-btn"
        disabled={busy}
        onClick={() => {
          setBusy(true)
          void onRevoke().finally(() => setBusy(false))
        }}
      >
        Confirm revoke
      </button>
      <button type="button" className="btn" disabled={busy} onClick={() => setArmed(false)}>
        Cancel
      </button>
    </span>
  )
}

function formatBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GiB`
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MiB`
  if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(1)} KiB`
  return `${n} B`
}

export default function SettingsView({ demo, onUnauthorized, onCatalogChange }: SettingsViewProps) {
  const [sites, setSites] = useState<Site[]>([])
  const [cameras, setCameras] = useState<Camera[]>([])
  const [tokens, setTokens] = useState<DeviceToken[]>([])
  const [retention, setRetention] = useState<RetentionStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Notes editor state — one camera at a time keeps it simple.
  const [notesFor, setNotesFor] = useState<string | null>(null)
  const [notesDraft, setNotesDraft] = useState('')

  // New-token form + the one-time plaintext reveal.
  const [newTokenName, setNewTokenName] = useState('')
  const [minting, setMinting] = useState(false)
  const [minted, setMinted] = useState<DeviceTokenCreated | null>(null)
  const [mintError, setMintError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const fail = useCallback(
    (err: unknown) => {
      if (err instanceof UnauthorizedError) onUnauthorized()
      else setError(err instanceof Error ? err.message : 'Request failed')
    },
    [onUnauthorized],
  )

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([
      api.getSites(true),
      api.getCameras(undefined, true),
      api.getDeviceTokens(),
      api.getRetentionStats(),
    ])
      .then(([s, c, t, r]) => {
        setSites(s)
        setCameras(c)
        setTokens(t)
        setRetention(r)
      })
      .catch(fail)
      .finally(() => setLoading(false))
  }, [fail])

  useEffect(() => {
    load()
  }, [load])

  const camerasBySite = useMemo(() => {
    const groups = new Map<string, Camera[]>()
    for (const c of cameras) {
      const list = groups.get(c.site_slug)
      if (list) list.push(c)
      else groups.set(c.site_slug, [c])
    }
    return groups
  }, [cameras])

  const saveSite = useCallback(
    async (site: Site, patch: api.SitePatch) => {
      try {
        const updated = await api.patchSite(site.id, patch)
        setSites((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
        onCatalogChange()
      } catch (err) {
        fail(err)
        throw err
      }
    },
    [fail, onCatalogChange],
  )

  const saveCamera = useCallback(
    async (camera: Camera, patch: api.CameraPatch) => {
      try {
        const updated = await api.patchCamera(camera.id, patch)
        setCameras((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
        onCatalogChange()
      } catch (err) {
        fail(err)
        throw err
      }
    },
    [fail, onCatalogChange],
  )

  const mintToken = useCallback(async () => {
    const name = newTokenName.trim()
    if (!name) return
    setMinting(true)
    setMintError(null)
    try {
      const created = await api.createDeviceToken(name)
      setMinted(created)
      setCopied(false)
      setNewTokenName('')
      setTokens((prev) =>
        [...prev, created].sort((a, b) => a.name.localeCompare(b.name)),
      )
    } catch (err) {
      if (err instanceof UnauthorizedError) onUnauthorized()
      else if (err instanceof api.ApiError && err.status === 409)
        setMintError('A token with that name already exists')
      else setMintError(err instanceof Error ? err.message : 'Failed to create token')
    } finally {
      setMinting(false)
    }
  }, [newTokenName, onUnauthorized])

  const revokeToken = useCallback(
    async (token: DeviceToken) => {
      try {
        await api.revokeDeviceToken(token.id)
        setTokens((prev) => prev.filter((t) => t.id !== token.id))
        setMinted((prev) => (prev?.id === token.id ? null : prev))
      } catch (err) {
        fail(err)
      }
    },
    [fail],
  )

  const copyMinted = useCallback(() => {
    if (!minted) return
    void navigator.clipboard.writeText(minted.token).then(() => setCopied(true))
  }, [minted])

  if (loading) {
    return (
      <div className="settings-view">
        <div className="grid-status">
          <span className="spinner" /> Loading settings…
        </div>
      </div>
    )
  }

  return (
    <div className="settings-view">
      {error && (
        <div className="grid-error" role="alert">
          {error}{' '}
          <button type="button" className="btn" onClick={load}>
            Retry
          </button>
        </div>
      )}

      <section className="settings-section" aria-label="Sites">
        <h2 className="site-heading">Sites</h2>
        <p className="settings-hint">
          Sites and cameras create themselves from the slugs the mesh sends; names here are
          display-only. Hiding removes an entry from the filter bars; its photos stay in the
          feed.
        </p>
        <ul className="settings-list">
          {sites.map((s) => (
            <li key={s.id} className={`settings-row${s.hidden ? ' row-hidden' : ''}`}>
              <EditableName
                value={s.name}
                label={`site ${s.slug}`}
                readOnly={demo}
                onSave={(name) => saveSite(s, { name })}
              />
              <span className="settings-slug">{s.slug}</span>
              <span className="settings-row-spacer" />
              {!demo && (
                <HideToggle
                  hidden={s.hidden}
                  label={`site ${s.name}`}
                  onToggle={() => saveSite(s, { hidden: !s.hidden })}
                />
              )}
            </li>
          ))}
          {sites.length === 0 && (
            <li className="settings-empty">No sites yet; they appear on first ingest.</li>
          )}
        </ul>
      </section>

      <section className="settings-section" aria-label="Cameras and nodes">
        <h2 className="site-heading">Cameras &amp; nodes</h2>
        {[...camerasBySite.entries()].map(([siteSlug, cams]) => (
          <div key={siteSlug} className="settings-subgroup">
            <h3 className="settings-subheading">
              {sites.find((s) => s.slug === siteSlug)?.name ?? siteSlug}
            </h3>
            <ul className="settings-list">
              {cams.map((c) => (
                <li key={c.id} className={`settings-row${c.hidden ? ' row-hidden' : ''}`}>
                  <div className="settings-row-main">
                    <div className="settings-row-title">
                      <EditableName
                        value={c.name}
                        label={`camera ${c.slug}`}
                        readOnly={demo}
                        onSave={(name) => saveCamera(c, { name })}
                      />
                      <span className={`kind-badge kind-${c.kind}`}>{c.kind}</span>
                      <span className="settings-slug">{c.slug}</span>
                    </div>
                    <div className="settings-row-sub">
                      {c.last_seen_at ? `last seen ${timeAgo(c.last_seen_at)}` : 'never seen'}
                      {c.last_battery_v != null && ` · ${c.last_battery_v.toFixed(2)} V`}
                    </div>
                    {demo ? (
                      c.notes ? <div className="notes-static">{c.notes}</div> : null
                    ) : notesFor === c.id ? (
                      <div className="notes-editor">
                        <textarea
                          className="settings-input notes-input"
                          value={notesDraft}
                          rows={2}
                          autoFocus
                          aria-label={`Notes for ${c.name}`}
                          onChange={(e) => setNotesDraft(e.target.value)}
                        />
                        <div className="notes-actions">
                          <button
                            type="button"
                            className="btn btn-primary"
                            onClick={() =>
                              void saveCamera(c, { notes: notesDraft }).then(() =>
                                setNotesFor(null),
                              )
                            }
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => setNotesFor(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="notes-line"
                        onClick={() => {
                          setNotesFor(c.id)
                          setNotesDraft(c.notes ?? '')
                        }}
                      >
                        {c.notes ? c.notes : 'Add notes…'}
                      </button>
                    )}
                  </div>
                  <span className="settings-row-spacer" />
                  {!demo && (
                    <HideToggle
                      hidden={c.hidden}
                      label={c.name}
                      onToggle={() => saveCamera(c, { hidden: !c.hidden })}
                    />
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {cameras.length === 0 && (
          <p className="settings-empty">No cameras yet; they appear on first ingest.</p>
        )}
      </section>

      <section className="settings-section" aria-label="Device tokens">
        <h2 className="site-heading">Device tokens</h2>
        <p className="settings-hint">
          Bearer tokens the mesh gateways use to push photos and telemetry. The token is shown
          once at creation, so store it in the gateway config immediately.
        </p>
        <ul className="settings-list">
          {tokens.map((t) => (
            <li key={t.id} className="settings-row">
              <KeyRound size={14} aria-hidden="true" className="token-icon" />
              <span className="editable-value">{t.name}</span>
              <span className="settings-row-sub">
                created {formatEastern(t.created_at)} ·{' '}
                {t.last_used_at ? `last used ${timeAgo(t.last_used_at)}` : 'never used'}
              </span>
              <span className="settings-row-spacer" />
              {!demo && <RevokeButton name={t.name} onRevoke={() => revokeToken(t)} />}
            </li>
          ))}
          {tokens.length === 0 && <li className="settings-empty">No device tokens.</li>}
        </ul>
        {!demo && minted && (
          <div className="token-reveal" role="status">
            <div className="token-reveal-head">
              Token for <strong>{minted.name}</strong> . Copy it now; it will not be shown
              again.
            </div>
            <div className="token-reveal-row">
              <code className="token-code">{minted.token}</code>
              <button type="button" className="btn" onClick={copyMinted}>
                {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
                {copied ? 'Copied' : 'Copy'}
              </button>
              <button type="button" className="btn" onClick={() => setMinted(null)}>
                Done
              </button>
            </div>
          </div>
        )}
        {!demo && (
          <div className="token-create">
            <input
              className="settings-input"
              placeholder="New token name (e.g. home-gateway)"
              value={newTokenName}
              disabled={minting}
              aria-label="New device token name"
              onChange={(e) => setNewTokenName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void mintToken()
              }}
            />
            <button
              type="button"
              className="btn btn-primary"
              disabled={minting || newTokenName.trim() === ''}
              onClick={() => void mintToken()}
            >
              <Plus size={14} aria-hidden="true" />
              Create token
            </button>
          </div>
        )}
        {!demo && mintError && (
          <p className="settings-error" role="alert">
            {mintError}
          </p>
        )}
      </section>

      {retention && (
        <section className="settings-section" aria-label="Retention">
          <h2 className="site-heading">Retention</h2>
          <p className="settings-hint">
            Photos expire {retention.ttl_days} days after capture unless saved. A nightly job
            deletes expired ones.
          </p>
          <div className="retention-facts">
            <div className="retention-fact">
              <span className="retention-num">{retention.photo_count}</span> photos
            </div>
            <div className="retention-fact">
              <span className="retention-num">{retention.kept_count}</span> saved forever
            </div>
            <div className="retention-fact">
              <span className="retention-num">{retention.expiring_soon}</span> expiring within
              7 days
            </div>
            <div className="retention-fact">
              <span className="retention-num">
                {formatBytes(retention.thumb_bytes + retention.full_bytes)}
              </span>{' '}
              stored
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
