import { useSyncExternalStore } from 'react'
import { normalizeIdRef } from './ids'
import type { View } from './types'

/**
 * Hand-rolled history-API router. The URL is the single source of truth:
 *
 *   /                    photo feed (filters in ?site=&camera=&saved=1&date=YYYY-MM-DD)
 *   /photos/<uuid>       photo detail overlay over the feed (query preserved)
 *   /nodes               nodes health view
 *   /nodes/<uuid>        node detail overlay over the nodes view
 *   /settings            management: sites, cameras, device tokens, retention
 *
 * Ids are UUIDs; deep links may use any unambiguous >=8-hex-char prefix (see
 * ids.ts). Anything unrecognized is normalized to `/` via replaceState at
 * startup.
 */

export interface Route {
  view: View
  photoId: string | null
  nodeId: string | null
  site: string | null
  cameraId: string | null
  keptOnly: boolean
  date: string | null
  tag: string | null
}

export interface FeedFilters {
  site: string | null
  cameraId: string | null
  keptOnly: boolean
  /** Eastern calendar day, YYYY-MM-DD — filters the feed to captures that day. */
  date: string | null
  /** Tag slug — filters the feed to photos carrying that tag. */
  tag: string | null
}

/** Dispatched after every programmatic navigate() so subscribers re-read the URL. */
const NAV_EVENT = 'trailcam:navigate'

// --- History index --------------------------------------------------------
//
// Each entry this app creates carries an index in history.state. pushState
// increments it, replaceState keeps it, and the browser restores it on
// back/forward. Index 0 is the entry the session landed on, so `index > 0`
// means "this session pushed an entry to get here" — the signal closeOverlay()
// uses to choose history.back() over a replace-navigate.

function currentIndex(): number {
  const state = history.state as { __tcIdx?: unknown } | null
  return typeof state?.__tcIdx === 'number' ? state.__tcIdx : 0
}

// --- URL parsing ------------------------------------------------------------

function parseFilters(search: string): FeedFilters {
  const params = new URLSearchParams(search)
  const site = params.get('site') || null
  const cameraRaw = params.get('camera')
  const cameraId =
    cameraRaw !== null && normalizeIdRef(cameraRaw) !== null ? cameraRaw : null
  const keptOnly = params.get('saved') === '1'
  const dateRaw = params.get('date')
  const date = dateRaw !== null && /^\d{4}-\d{2}-\d{2}$/.test(dateRaw) ? dateRaw : null
  const tag = params.get('tag') || null
  return { site, cameraId, keptOnly, date, tag }
}

/** Parse a pathname + search into a Route, or null when unrecognized. */
function tryParse(pathname: string, search: string): Route | null {
  const filters = parseFilters(search)
  const segments = pathname.split('/').filter(Boolean)

  if (segments.length === 0) {
    return { view: 'photos', photoId: null, nodeId: null, ...filters }
  }
  if (segments[0] === 'photos' && segments.length === 2 && segments[1]) {
    let photoId: string
    try {
      photoId = decodeURIComponent(segments[1])
    } catch {
      return null
    }
    return { view: 'photos', photoId, nodeId: null, ...filters }
  }
  if (segments[0] === 'nodes') {
    if (segments.length === 1) {
      return { view: 'nodes', photoId: null, nodeId: null, ...filters }
    }
    if (segments.length === 2 && segments[1]) {
      let nodeId: string
      try {
        nodeId = decodeURIComponent(segments[1])
      } catch {
        return null
      }
      if (normalizeIdRef(nodeId) !== null) {
        return { view: 'nodes', photoId: null, nodeId, ...filters }
      }
      // Unrecognized ref (e.g. a pre-UUID integer link) — land on the list.
      return { view: 'nodes', photoId: null, nodeId: null, ...filters }
    }
  }
  if (segments[0] === 'settings' && segments.length === 1) {
    return { view: 'settings', photoId: null, nodeId: null, ...filters }
  }
  return null
}

// Parse once per URL change: the snapshot is memoized on location.href so
// repeated getSnapshot() calls return a referentially stable Route and
// useSyncExternalStore doesn't loop or re-render without a real change.
let cachedHref: string | null = null
let cachedRoute: Route = { view: 'photos', photoId: null, nodeId: null, site: null, cameraId: null, keptOnly: false, date: null, tag: null }

function getSnapshot(): Route {
  if (cachedHref !== location.href) {
    cachedHref = location.href
    cachedRoute =
      tryParse(location.pathname, location.search) ??
      // Defensive: unrecognized URLs are normalized at startup and navigate()
      // only ever receives app-built URLs, so this fallback should not fire.
      { view: 'photos', photoId: null, nodeId: null, ...parseFilters(location.search) }
  }
  return cachedRoute
}

// --- Startup normalization --------------------------------------------------

// Seed the history index and normalize an unrecognized deep link to `/`
// (keeping the query string — filters are orthogonal to the path).
if (tryParse(location.pathname, location.search) === null) {
  history.replaceState({ __tcIdx: currentIndex() }, '', `/${location.search}`)
} else if (typeof (history.state as { __tcIdx?: unknown } | null)?.__tcIdx !== 'number') {
  history.replaceState({ __tcIdx: 0 }, '', location.href)
}

// --- Navigation --------------------------------------------------------------

export function navigate(to: string, opts?: { replace?: boolean }): void {
  if (opts?.replace) {
    history.replaceState({ __tcIdx: currentIndex() }, '', to)
  } else {
    history.pushState({ __tcIdx: currentIndex() + 1 }, '', to)
  }
  window.dispatchEvent(new Event(NAV_EVENT))
}

/**
 * Close an overlay: if this session pushed the current entry, go back (so the
 * forward button still works and history stays tidy); otherwise — deep link or
 * refresh landed directly on it — replace-navigate to the fallback URL.
 */
export function closeOverlay(fallback: string): void {
  if (currentIndex() > 0) {
    history.back()
  } else {
    navigate(fallback, { replace: true })
  }
}

// --- Subscription -------------------------------------------------------------

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange)
  window.addEventListener(NAV_EVENT, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(NAV_EVENT, onChange)
  }
}

/** Current route, re-rendering on popstate and programmatic navigation. */
export function useRoute(): Route {
  return useSyncExternalStore(subscribe, getSnapshot)
}

// --- URL builders --------------------------------------------------------------

function feedSearch(filters: FeedFilters): string {
  const params = new URLSearchParams()
  if (filters.site) params.set('site', filters.site)
  if (filters.cameraId != null) params.set('camera', filters.cameraId)
  if (filters.keptOnly) params.set('saved', '1')
  if (filters.date) params.set('date', filters.date)
  if (filters.tag) params.set('tag', filters.tag)
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export function feedUrl(filters: FeedFilters): string {
  return `/${feedSearch(filters)}`
}

export function photoUrl(id: string, filters: FeedFilters): string {
  return `/photos/${encodeURIComponent(id)}${feedSearch(filters)}`
}

export function nodesUrl(): string {
  return '/nodes'
}

export function settingsUrl(): string {
  return '/settings'
}

export function nodeUrl(id: string): string {
  return `/nodes/${encodeURIComponent(id)}`
}
