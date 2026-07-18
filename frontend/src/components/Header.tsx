import { Images, Layers, LogOut, Map as MapIcon, RadioTower, Settings, SquareCheck, Star } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { Camera, Me, Site, TagCount, View } from '../types'

interface HeaderProps {
  me: Me
  /** Public read-only demo: hide write controls, show a read-only badge. */
  demo: boolean
  view: View
  onViewChange: (view: View) => void
  sites: Site[]
  site: string | null
  onSiteChange: (slug: string | null) => void
  cameras: Camera[]
  cameraId: string | null
  onCameraChange: (id: string | null) => void
  keptOnly: boolean
  onKeptOnlyChange: (value: boolean) => void
  tags: TagCount[]
  tag: string | null
  onTagChange: (value: string | null) => void
  /** Ungrouped feed (every frame its own tile); grouped is the default. */
  flat: boolean
  onFlatChange: (value: boolean) => void
  selecting: boolean
  onToggleSelecting: () => void
  onSignOut: () => void
}

const VIEWS: { view: View; label: string; Icon: LucideIcon }[] = [
  { view: 'photos', label: 'Photos', Icon: Images },
  { view: 'nodes', label: 'Nodes', Icon: RadioTower },
  { view: 'survey', label: 'Survey', Icon: MapIcon },
  { view: 'settings', label: 'Settings', Icon: Settings },
]

export default function Header({
  me,
  demo,
  view,
  onViewChange,
  sites,
  site,
  onSiteChange,
  cameras,
  cameraId,
  onCameraChange,
  keptOnly,
  onKeptOnlyChange,
  tags,
  tag,
  onTagChange,
  flat,
  onFlatChange,
  selecting,
  onToggleSelecting,
  onSignOut,
}: HeaderProps) {
  const initial = (me.name || me.email).trim().charAt(0).toUpperCase() || '?'

  return (
    <header className="header">
      <div className="header-row">
        <h1 className="app-title">🦌 MeshCam</h1>
        {demo && (
          <span className="kind-badge demo-badge" title="Read-only public demo">
            Demo · read-only
          </span>
        )}
        {demo && (
          <a className="get-meshcam" href="https://getmeshcam.com">
            Get MeshCam &rarr;
          </a>
        )}
        <div className="header-spacer" />
        {view === 'photos' && (
          <button
            type="button"
            className={`btn saved-toggle${keptOnly ? ' active' : ''}`}
            aria-pressed={keptOnly}
            aria-label="Saved photos only"
            onClick={() => onKeptOnlyChange(!keptOnly)}
          >
            <Star
              size={16}
              fill={keptOnly ? 'currentColor' : 'none'}
              aria-hidden="true"
            />
            <span className="btn-label">Saved</span>
          </button>
        )}
        <details className="user-menu">
          <summary className="user-avatar" aria-label="Account menu">
            {initial}
          </summary>
          <div className="user-menu-panel">
            <div className="user-name">{me.name}</div>
            <div className="user-email">{me.email}</div>
            {demo ? (
              <div className="user-demo-note">
                Read-only public demo ·{' '}
                <a href="https://getmeshcam.com">getmeshcam.com</a>
              </div>
            ) : (
              <button type="button" className="btn signout-btn" onClick={onSignOut}>
                <LogOut size={16} aria-hidden="true" />
                Sign out
              </button>
            )}
          </div>
        </details>
      </div>
      <div className="header-row header-tabs">
        <nav className="view-tabs" aria-label="View">
          {VIEWS.map((v) => (
            <button
              key={v.view}
              type="button"
              className={`view-tab${view === v.view ? ' active' : ''}`}
              aria-pressed={view === v.view}
              onClick={() => onViewChange(v.view)}
            >
              <v.Icon size={16} aria-hidden="true" />
              {v.label}
            </button>
          ))}
        </nav>
      </div>
      {view === 'photos' && (
        <div className="header-row header-filters">
          <div className="site-pills" role="group" aria-label="Site filter">
            <button
              type="button"
              className={`pill${site === null ? ' active' : ''}`}
              onClick={() => onSiteChange(null)}
            >
              All
            </button>
            {sites.map((s) => (
              <button
                key={s.slug}
                type="button"
                className={`pill${site === s.slug ? ' active' : ''}`}
                onClick={() => onSiteChange(s.slug)}
              >
                {s.name}
              </button>
            ))}
          </div>
          <select
            className="camera-select"
            aria-label="Camera filter"
            value={cameraId ?? ''}
            onChange={(e) => onCameraChange(e.target.value === '' ? null : e.target.value)}
          >
            <option value="">All cameras</option>
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          {(tags.length > 0 || tag !== null) && (
            <select
              className="camera-select tag-select"
              aria-label="Tag filter"
              value={tag ?? ''}
              onChange={(e) => onTagChange(e.target.value === '' ? null : e.target.value)}
            >
              <option value="">All tags</option>
              {tags.map((t) => (
                <option key={t.slug} value={t.slug}>
                  {t.name} ({t.count})
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className={`btn saved-toggle${!flat ? ' active' : ''}`}
            aria-pressed={!flat}
            aria-label="Group burst photos into sightings"
            title={flat ? 'Group burst photos into sightings' : 'Show every frame separately'}
            onClick={() => onFlatChange(!flat)}
          >
            <Layers size={16} aria-hidden="true" />
            <span className="btn-label">Grouped</span>
          </button>
          {!demo && (
            <button
              type="button"
              className={`btn select-toggle${selecting ? ' active' : ''}`}
              aria-pressed={selecting}
              aria-label="Select photos"
              onClick={onToggleSelecting}
            >
              <SquareCheck size={16} aria-hidden="true" />
              <span className="btn-label">Select</span>
            </button>
          )}
        </div>
      )}
    </header>
  )
}
