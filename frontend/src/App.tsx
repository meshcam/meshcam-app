import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import { UnauthorizedError } from './api'
import BulkBar from './components/BulkBar'
import Header from './components/Header'
import NodesView from './components/NodesView'
import PhotoDetail from './components/PhotoDetail'
import PhotoGrid from './components/PhotoGrid'
import SettingsView from './components/SettingsView'
import Splash from './components/Splash'
import { easternDayBounds, easternDayKey } from './format'
import { idMatchesRef } from './ids'
import { live } from './live'
import {
  closeOverlay,
  feedUrl,
  navigate,
  nodesUrl,
  photoUrl,
  settingsUrl,
  useRoute,
} from './router'
import type { FeedFilters } from './router'
import type { Camera, Me, Photo, Site, TagCount, View } from './types'

type AuthState =
  | { status: 'loading' }
  | { status: 'anon' }
  | { status: 'ok'; me: Me }

const PAGE_SIZE = 50
/** Start loading the next page when detail navigation gets this close to the end. */
const PREFETCH_MARGIN = 5

const TITLE_BASE = 'MeshCam'

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'loading' })
  const route = useRoute()
  const view = route.view
  const [sites, setSites] = useState<Site[]>([])
  const [cameras, setCameras] = useState<Camera[]>([])
  const [tags, setTags] = useState<TagCount[]>([])
  // Bumped by the settings view after renames/hides so the filter lists refetch.
  const [catalogVersion, setCatalogVersion] = useState(0)
  // Bumped after tag add/remove so the tag filter list (and counts) refetch.
  const [tagsVersion, setTagsVersion] = useState(0)

  // Filters — the URL is the source of truth on photo routes. `/nodes` carries
  // no filter params, so the last photo-route values are kept sticky in a ref;
  // otherwise a Photos → Nodes → back round-trip would flap the filter values
  // and needlessly reset/refetch the feed.
  const filtersRef = useRef<FeedFilters>({
    site: null,
    cameraId: null,
    keptOnly: false,
    date: null,
    tag: null,
  })
  const onPhotosRoute = view === 'photos'
  const site = onPhotosRoute ? route.site : filtersRef.current.site
  const cameraId = onPhotosRoute ? route.cameraId : filtersRef.current.cameraId
  const keptOnly = onPhotosRoute ? route.keptOnly : filtersRef.current.keptOnly
  const date = onPhotosRoute ? route.date : filtersRef.current.date
  const tag = onPhotosRoute ? route.tag : filtersRef.current.tag
  useEffect(() => {
    filtersRef.current = { site, cameraId, keptOnly, date, tag }
  })

  // Photo feed
  const [photos, setPhotos] = useState<Photo[]>([])
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [feedError, setFeedError] = useState<string | null>(null)
  // Deep-linked photo fetched individually while (or because) it isn't in the feed.
  const [fetchedPhoto, setFetchedPhoto] = useState<Photo | null>(null)

  // Mutable feed bookkeeping (cursor + in-flight guard + generation counter
  // so stale responses from a superseded filter combination are discarded).
  const feedRef = useRef({ gen: 0, cursor: null as string | null, busy: false })

  // Multi-select for bulk save/delete. Selection only means anything against
  // the feed it was made on, so it clears whenever the feed resets.
  const [selecting, setSelecting] = useState(false)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)

  // --- Auth ---------------------------------------------------------------

  useEffect(() => {
    let cancelled = false
    api
      .getMe()
      .then((me) => {
        if (!cancelled) setAuth({ status: 'ok', me })
      })
      .catch(() => {
        if (!cancelled) setAuth({ status: 'anon' })
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleUnauthorized = useCallback(() => {
    setAuth({ status: 'anon' })
  }, [])

  // --- Live updates (one SSE connection for the whole tab) ------------------

  useEffect(() => {
    if (auth.status !== 'ok') return
    live.connect()
    const offPhoto = live.subscribe('photo', (d) => {
      const p = d as Photo & { change: 'new' | 'updated' }
      setPhotos((prev) => {
        if (prev.some((x) => x.id === p.id)) {
          return prev.map((x) => (x.id === p.id ? p : x))
        }
        // Prepend only when the new photo matches the active filters.
        const f = filtersRef.current
        if (f.site && p.site_slug !== f.site) return prev
        if (f.cameraId != null && !idMatchesRef(p.camera_id, f.cameraId)) return prev
        if (f.keptOnly && !p.keep) return prev
        if (f.date && easternDayKey(p.captured_at) !== f.date) return prev
        if (f.tag && !p.tags.some((t) => t.slug === f.tag)) return prev
        return [p, ...prev]
      })
      setFetchedPhoto((prev) => (prev?.id === p.id ? p : prev))
    })
    const offRemoved = live.subscribe('photo_removed', (d) => {
      const { id } = d as { id: string }
      setPhotos((prev) => prev.filter((p) => p.id !== id))
    })
    return () => {
      offPhoto()
      offRemoved()
      live.disconnect()
    }
  }, [auth.status])

  // --- Sites & cameras ----------------------------------------------------

  useEffect(() => {
    if (auth.status !== 'ok') return
    let cancelled = false
    api
      .getSites()
      .then((data) => {
        if (!cancelled) setSites(data)
      })
      .catch((err: unknown) => {
        if (err instanceof UnauthorizedError) handleUnauthorized()
      })
    return () => {
      cancelled = true
    }
  }, [auth.status, handleUnauthorized, catalogVersion])

  useEffect(() => {
    if (auth.status !== 'ok') return
    let cancelled = false
    api
      .getCameras(site ?? undefined)
      .then((data) => {
        if (!cancelled) setCameras(data)
      })
      .catch((err: unknown) => {
        if (err instanceof UnauthorizedError) handleUnauthorized()
      })
    return () => {
      cancelled = true
    }
  }, [auth.status, site, handleUnauthorized, catalogVersion])

  useEffect(() => {
    if (auth.status !== 'ok') return
    let cancelled = false
    api
      .getTags()
      .then((data) => {
        if (!cancelled) setTags(data)
      })
      .catch((err: unknown) => {
        if (err instanceof UnauthorizedError) handleUnauthorized()
      })
    return () => {
      cancelled = true
    }
  }, [auth.status, handleUnauthorized, tagsVersion])

  // --- Photo feed ---------------------------------------------------------

  const loadPhotos = useCallback(
    async (reset: boolean) => {
      const feed = feedRef.current
      if (reset) {
        feed.gen += 1
        feed.cursor = null
        feed.busy = false
      }
      if (feed.busy) return
      const gen = feed.gen
      feed.busy = true
      setLoading(true)
      setFeedError(null)
      if (reset) {
        setPhotos([])
        setHasMore(true)
        setSelectedIds(new Set())
      }
      try {
        const bounds = date ? easternDayBounds(date) : null
        const page = await api.getPhotos({
          site: site ?? undefined,
          cameraId: cameraId ?? undefined,
          kept: keptOnly || undefined,
          tag: tag ?? undefined,
          capturedAfter: bounds?.start,
          capturedBefore: bounds?.end,
          before: feed.cursor ?? undefined,
          limit: PAGE_SIZE,
        })
        if (feedRef.current.gen !== gen) return
        feed.cursor = page.next_cursor
        setPhotos((prev) => (reset ? page.items : [...prev, ...page.items]))
        setHasMore(page.next_cursor !== null)
      } catch (err: unknown) {
        if (feedRef.current.gen !== gen) return
        if (err instanceof UnauthorizedError) {
          handleUnauthorized()
          return
        }
        setFeedError(err instanceof Error ? err.message : 'Failed to load photos')
      } finally {
        if (feedRef.current.gen === gen) {
          feed.busy = false
          setLoading(false)
        }
      }
    },
    [site, cameraId, keptOnly, date, tag, handleUnauthorized],
  )

  useEffect(() => {
    if (auth.status !== 'ok') return
    void loadPhotos(true)
  }, [auth.status, loadPhotos])

  const loadMore = useCallback(() => {
    if (!loading && hasMore && !feedError) void loadPhotos(false)
  }, [loading, hasMore, feedError, loadPhotos])

  const retry = useCallback(() => {
    void loadPhotos(false)
  }, [loadPhotos])

  // --- Filter handlers (replace, not push; a feed URL clears an open overlay)

  const handleSiteChange = useCallback(
    (slug: string | null) => {
      // Camera list is site-scoped; a stale id would 404-filter, so clear it.
      navigate(feedUrl({ site: slug, cameraId: null, keptOnly, date, tag }), { replace: true })
    },
    [keptOnly, date, tag],
  )

  const handleCameraChange = useCallback(
    (id: string | null) => {
      navigate(feedUrl({ site, cameraId: id, keptOnly, date, tag }), { replace: true })
    },
    [site, keptOnly, date, tag],
  )

  const handleKeptOnlyChange = useCallback(
    (value: boolean) => {
      navigate(feedUrl({ site, cameraId, keptOnly: value, date, tag }), { replace: true })
    },
    [site, cameraId, date, tag],
  )

  const handleDateChange = useCallback(
    (value: string | null) => {
      navigate(feedUrl({ site, cameraId, keptOnly, date: value, tag }), { replace: true })
    },
    [site, cameraId, keptOnly, tag],
  )

  const handleTagChange = useCallback(
    (value: string | null) => {
      navigate(feedUrl({ site, cameraId, keptOnly, date, tag: value }), { replace: true })
    },
    [site, cameraId, keptOnly, date],
  )

  const handleViewChange = useCallback((next: View) => {
    const to = next === 'photos' ? '/' : next === 'nodes' ? nodesUrl() : settingsUrl()
    if (location.pathname + location.search === to) return
    navigate(to)
  }, [])

  // --- Detail overlay -----------------------------------------------------

  // The URL id may be a generous prefix ref (ids.ts), so match accordingly.
  const photoId = view === 'photos' ? route.photoId : null
  const selectedIndex =
    photoId === null ? -1 : photos.findIndex((p) => idMatchesRef(p.id, photoId))
  const inFeed = selectedIndex >= 0
  const selected = inFeed
    ? photos[selectedIndex]
    : fetchedPhoto !== null && idMatchesRef(fetchedPhoto.id, photoId ?? '')
      ? fetchedPhoto
      : null

  // Deep link: the routed photo isn't in the (possibly still-loading) feed —
  // fetch it individually so the overlay shows right away. A prefix ref is
  // canonicalized to the full id once resolved. On 404 (or any non-auth
  // failure) silently replace-navigate back to the feed.
  useEffect(() => {
    if (auth.status !== 'ok' || photoId === null || inFeed) return
    if (fetchedPhoto !== null && idMatchesRef(fetchedPhoto.id, photoId)) return
    let cancelled = false
    api
      .getPhoto(photoId)
      .then((photo) => {
        if (cancelled) return
        setFetchedPhoto(photo)
        if (photo.id !== photoId) {
          navigate(photoUrl(photo.id, { site, cameraId, keptOnly, date, tag }), { replace: true })
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof UnauthorizedError) {
          handleUnauthorized()
          return
        }
        navigate(feedUrl({ site, cameraId, keptOnly, date, tag }), { replace: true })
      })
    return () => {
      cancelled = true
    }
  }, [
    auth.status,
    photoId,
    inFeed,
    fetchedPhoto,
    site,
    cameraId,
    keptOnly,
    date,
    tag,
    handleUnauthorized,
  ])

  // Prefetch the next page while paging through the overlay near the end.
  useEffect(() => {
    if (selectedIndex < 0) return
    if (selectedIndex >= photos.length - PREFETCH_MARGIN && hasMore && !loading) {
      void loadPhotos(false)
    }
  }, [selectedIndex, photos.length, hasMore, loading, loadPhotos])

  const openPhoto = useCallback(
    (id: string) => {
      navigate(photoUrl(id, { site, cameraId, keptOnly, date, tag }))
    },
    [site, cameraId, keptOnly, date, tag],
  )

  // Prev/next replace the current entry — paging through 50 photos must not
  // create 50 history entries.
  const showPrev = useCallback(() => {
    if (selectedIndex > 0) {
      navigate(photoUrl(photos[selectedIndex - 1].id, { site, cameraId, keptOnly, date, tag }), {
        replace: true,
      })
    }
  }, [selectedIndex, photos, site, cameraId, keptOnly, date, tag])

  const showNext = useCallback(() => {
    if (selectedIndex >= 0 && selectedIndex < photos.length - 1) {
      navigate(photoUrl(photos[selectedIndex + 1].id, { site, cameraId, keptOnly, date, tag }), {
        replace: true,
      })
    }
  }, [selectedIndex, photos, site, cameraId, keptOnly, date, tag])

  const closeDetail = useCallback(() => {
    closeOverlay(feedUrl({ site, cameraId, keptOnly, date, tag }))
  }, [site, cameraId, keptOnly, date, tag])

  const toggleKeep = useCallback(
    async (photo: Photo) => {
      try {
        const updated = await api.setKeep(photo.id, !photo.keep)
        setPhotos((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
        setFetchedPhoto((prev) => (prev?.id === updated.id ? updated : prev))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
        else throw err
      }
    },
    [handleUnauthorized],
  )

  const requestFull = useCallback(
    async (photo: Photo, quality: 'standard' | 'max') => {
      try {
        const updated = await api.requestFull(photo.id, quality)
        setPhotos((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
        setFetchedPhoto((prev) => (prev?.id === updated.id ? updated : prev))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
        else throw err
      }
    },
    [handleUnauthorized],
  )

  // Tag mutations sync both photo stores and refresh the tag filter counts.
  const applyPhotoUpdate = useCallback((updated: Photo) => {
    setPhotos((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
    setFetchedPhoto((prev) => (prev?.id === updated.id ? updated : prev))
    setTagsVersion((v) => v + 1)
  }, [])

  const addTag = useCallback(
    async (photo: Photo, name: string) => {
      try {
        applyPhotoUpdate(await api.addPhotoTag(photo.id, name))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
        else throw err
      }
    },
    [applyPhotoUpdate, handleUnauthorized],
  )

  const removeTag = useCallback(
    async (photo: Photo, slug: string) => {
      try {
        applyPhotoUpdate(await api.removePhotoTag(photo.id, slug))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
        else throw err
      }
    },
    [applyPhotoUpdate, handleUnauthorized],
  )

  // Re-fetch one photo (e.g. after its full-res arrives) and sync both stores.
  const refreshPhoto = useCallback(
    async (photo: Photo) => {
      try {
        const updated = await api.getPhoto(photo.id)
        setPhotos((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
        setFetchedPhoto((prev) => (prev?.id === updated.id ? updated : prev))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
      }
    },
    [handleUnauthorized],
  )

  const deletePhoto = useCallback(
    async (photo: Photo) => {
      try {
        await api.deletePhoto(photo.id)
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) {
          handleUnauthorized()
          return
        }
        throw err
      }
      const idx = photos.findIndex((p) => p.id === photo.id)
      const remaining = photos.filter((p) => p.id !== photo.id)
      setPhotos(remaining)
      setFetchedPhoto((prev) => (prev?.id === photo.id ? null : prev))
      // Move (replace) to the adjacent photo's URL, or back to the feed when
      // none remain (or the deleted photo was a deep link outside the feed).
      const filters = { site, cameraId, keptOnly, date, tag }
      if (idx < 0 || remaining.length === 0) {
        navigate(feedUrl(filters), { replace: true })
      } else {
        const nextIdx = Math.min(Math.max(idx, 0), remaining.length - 1)
        navigate(photoUrl(remaining[nextIdx].id, filters), { replace: true })
      }
    },
    [photos, site, cameraId, keptOnly, date, tag, handleUnauthorized],
  )

  // --- Bulk selection -------------------------------------------------------

  const toggleSelecting = useCallback(() => {
    setSelecting((prev) => !prev)
    setSelectedIds(new Set())
  }, [])

  const toggleSelected = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  /** Run `fn` over ids a few at a time — kind to the backend, still quick. */
  const forSelected = useCallback(
    async (fn: (id: string) => Promise<unknown>) => {
      const ids = [...selectedIds]
      const CHUNK = 8
      for (let i = 0; i < ids.length; i += CHUNK) {
        await Promise.all(ids.slice(i, i + CHUNK).map(fn))
      }
    },
    [selectedIds],
  )

  const bulkKeep = useCallback(async () => {
    setBulkBusy(true)
    const updates = new Map<string, Photo>()
    let failed = false
    try {
      await forSelected(async (id) => {
        updates.set(id, await api.setKeep(id, true))
      })
    } catch (err: unknown) {
      failed = true
      if (err instanceof UnauthorizedError) handleUnauthorized()
    }
    // Apply whatever succeeded even on partial failure.
    setPhotos((prev) => prev.map((p) => updates.get(p.id) ?? p))
    if (!failed) {
      setSelecting(false)
      setSelectedIds(new Set())
    }
    setBulkBusy(false)
  }, [forSelected, handleUnauthorized])

  const bulkDelete = useCallback(async () => {
    setBulkBusy(true)
    const deleted = new Set<string>()
    let failed = false
    try {
      await forSelected(async (id) => {
        await api.deletePhoto(id)
        deleted.add(id)
      })
    } catch (err: unknown) {
      failed = true
      if (err instanceof UnauthorizedError) handleUnauthorized()
    }
    setPhotos((prev) => prev.filter((p) => !deleted.has(p.id)))
    setSelectedIds((prev) => {
      const next = new Set(prev)
      for (const id of deleted) next.delete(id)
      return next
    })
    if (!failed) setSelecting(false)
    setBulkBusy(false)
  }, [forSelected, handleUnauthorized])

  const bulkTag = useCallback(
    async (name: string) => {
      setBulkBusy(true)
      const updates = new Map<string, Photo>()
      try {
        await forSelected(async (id) => {
          updates.set(id, await api.addPhotoTag(id, name))
        })
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
      }
      setPhotos((prev) => prev.map((p) => updates.get(p.id) ?? p))
      setTagsVersion((v) => v + 1)
      setBulkBusy(false)
    },
    [forSelected, handleUnauthorized],
  )

  // --- Document title -------------------------------------------------------

  useEffect(() => {
    if (selected) {
      document.title = `${TITLE_BASE} · ${selected.camera_name}`
    } else if (view === 'nodes') {
      document.title = `${TITLE_BASE} · Nodes`
    } else if (view === 'settings') {
      document.title = `${TITLE_BASE} · Settings`
    } else {
      document.title = TITLE_BASE
    }
  }, [view, selected])

  // --- Render -------------------------------------------------------------

  if (auth.status === 'loading') {
    return (
      <div className="app-loading" aria-label="Loading">
        <span className="spinner" />
      </div>
    )
  }

  if (auth.status === 'anon') {
    return <Splash />
  }

  const demo = auth.me.demo

  return (
    <div className="app">
      <Header
        me={auth.me}
        demo={demo}
        view={view}
        onViewChange={handleViewChange}
        sites={sites}
        site={site}
        onSiteChange={handleSiteChange}
        cameras={cameras}
        cameraId={cameraId}
        onCameraChange={handleCameraChange}
        keptOnly={keptOnly}
        onKeptOnlyChange={handleKeptOnlyChange}
        date={date}
        onDateChange={handleDateChange}
        tags={tags}
        tag={tag}
        onTagChange={handleTagChange}
        selecting={selecting}
        onToggleSelecting={toggleSelecting}
        onSignOut={() => void api.signOut()}
      />
      {view === 'photos' ? (
        <PhotoGrid
          photos={photos}
          loading={loading}
          hasMore={hasMore}
          error={feedError}
          onLoadMore={loadMore}
          onRetry={retry}
          onSelect={openPhoto}
          selecting={selecting}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelected}
        />
      ) : view === 'nodes' ? (
        <NodesView
          sites={sites}
          nodeId={route.nodeId}
          demo={demo}
          onUnauthorized={handleUnauthorized}
        />
      ) : (
        <SettingsView
          demo={demo}
          onUnauthorized={handleUnauthorized}
          onCatalogChange={() => setCatalogVersion((v) => v + 1)}
        />
      )}
      {view === 'photos' && selecting && !demo && (
        <BulkBar
          count={selectedIds.size}
          busy={bulkBusy}
          tags={tags}
          onSave={() => void bulkKeep()}
          onTag={(name) => void bulkTag(name)}
          onDelete={() => void bulkDelete()}
          onCancel={toggleSelecting}
        />
      )}
      {view === 'photos' && !selecting && selected && (
        <PhotoDetail
          photo={selected}
          demo={demo}
          hasPrev={selectedIndex > 0}
          hasNext={inFeed && selectedIndex < photos.length - 1}
          onPrev={showPrev}
          onNext={showNext}
          onClose={closeDetail}
          onToggleKeep={() => toggleKeep(selected)}
          onRequestFull={(quality) => requestFull(selected, quality)}
          onRefresh={() => refreshPhoto(selected)}
          onDelete={() => deletePhoto(selected)}
          allTags={tags}
          onAddTag={(name) => addTag(selected, name)}
          onRemoveTag={(slug) => removeTag(selected, slug)}
        />
      )}
    </div>
  )
}
