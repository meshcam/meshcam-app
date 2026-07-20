import { ChevronsUp } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from './api'
import { UnauthorizedError } from './api'
import BulkBar from './components/BulkBar'
import Header from './components/Header'
import NodesView from './components/NodesView'
import PhotoDetail from './components/PhotoDetail'
import PhotoGrid from './components/PhotoGrid'
import PhotoTimeline from './components/PhotoTimeline'
import SettingsView from './components/SettingsView'
import SightingGrid from './components/SightingGrid'
import Splash from './components/Splash'
import TimeScrubber from './components/TimeScrubber'
import SurveyView from './components/SurveyView'
import { easternDayBounds, easternDayKey } from './format'
import { idMatchesRef } from './ids'
import { live } from './live'
import {
  closeOverlay,
  EMPTY_SURVEY,
  feedUrl,
  navigate,
  nodesUrl,
  photoUrl,
  settingsUrl,
  surveyUrl,
  useRoute,
} from './router'
import type { FeedFilters } from './router'
import type { Camera, HistogramBucket, Me, Photo, Sighting, Site, TagCount, View } from './types'

type AuthState =
  | { status: 'loading' }
  | { status: 'anon' }
  | { status: 'ok'; me: Me }

const PAGE_SIZE = 50
/** Start loading the next page when detail navigation gets this close to the end. */
const PREFETCH_MARGIN = 5

const TITLE_BASE = 'MeshCam'

/** Whether a live photo belongs on screen under the active filters. */
function matchesFilters(p: Photo, f: FeedFilters): boolean {
  if (f.site && p.site_slug !== f.site) return false
  if (f.cameraId != null && !idMatchesRef(p.camera_id, f.cameraId)) return false
  if (f.keptOnly && !p.keep) return false
  if (f.date && easternDayKey(p.captured_at) !== f.date) return false
  if (f.tag && !p.tags.some((t) => t.slug === f.tag)) return false
  if (f.from && Date.parse(p.captured_at) < Date.parse(f.from)) return false
  if (f.to && Date.parse(p.captured_at) >= Date.parse(f.to)) return false
  return true
}

const laterIso = (a: string, b: string) => (Date.parse(a) >= Date.parse(b) ? a : b)
const earlierIso = (a: string, b: string) => (Date.parse(a) <= Date.parse(b) ? a : b)

/** Capture-chronological member order — matches /sightings/{id}/photos. */
function byCapture(a: Photo, b: Photo): number {
  return Date.parse(a.captured_at) - Date.parse(b.captured_at) || a.id.localeCompare(b.id)
}

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
    flat: false,
    from: null,
    to: null,
    at: null,
  })
  const onPhotosRoute = view === 'photos'
  const site = onPhotosRoute ? route.site : filtersRef.current.site
  const cameraId = onPhotosRoute ? route.cameraId : filtersRef.current.cameraId
  const keptOnly = onPhotosRoute ? route.keptOnly : filtersRef.current.keptOnly
  const date = onPhotosRoute ? route.date : filtersRef.current.date
  const tag = onPhotosRoute ? route.tag : filtersRef.current.tag
  const flat = onPhotosRoute ? route.flat : filtersRef.current.flat
  const from = onPhotosRoute ? route.from : filtersRef.current.from
  const to = onPhotosRoute ? route.to : filtersRef.current.to
  const at = onPhotosRoute ? route.at : filtersRef.current.at
  const filters = useMemo<FeedFilters>(
    () => ({ site, cameraId, keptOnly, date, tag, flat, from, to, at }),
    [site, cameraId, keptOnly, date, tag, flat, from, to, at],
  )
  useEffect(() => {
    filtersRef.current = filters
  })

  // Photo feed — `photos` carries the flat feed, `sightings` the grouped one
  // (the default). Only the active mode's array is populated.
  const [photos, setPhotos] = useState<Photo[]>([])
  const [sightings, setSightings] = useState<Sighting[]>([])
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  // Anchored (?at=) feed: newer items exist above what's loaded. While true,
  // the top of the list is NOT "now", so SSE prepends are held off and a
  // "Jump to latest" pill offers the way back.
  const [hasMoreUp, setHasMoreUp] = useState(false)
  const [loadingUp, setLoadingUp] = useState(false)
  const [feedError, setFeedError] = useState<string | null>(null)
  // Deep-linked photo fetched individually while (or because) it isn't in the feed.
  const [fetchedPhoto, setFetchedPhoto] = useState<Photo | null>(null)
  // Grouped mode: every frame of the open sighting, capture-chronological —
  // the list the detail overlay pages through.
  const [sightingPhotos, setSightingPhotos] = useState<{ sid: string; photos: Photo[] } | null>(
    null,
  )
  // Mirror for SSE handlers (they hold stale closures; same trick as filtersRef).
  const sightingsRef = useRef<Sighting[]>([])
  useEffect(() => {
    sightingsRef.current = sightings
  }, [sightings])

  // Mutable feed bookkeeping (cursors + in-flight guards + generation counter
  // so stale responses from a superseded filter combination are discarded).
  const feedRef = useRef({
    gen: 0,
    cursor: null as string | null,
    upCursor: null as string | null,
    busy: false,
    busyUp: false,
  })
  // Mirror for the SSE handlers (stale-closure trick, same as filtersRef).
  const anchoredRef = useRef(false)
  useEffect(() => {
    anchoredRef.current = hasMoreUp
  }, [hasMoreUp])

  // Multi-select for bulk save/delete. Selection only means anything against
  // the feed it was made on, so it clears whenever the feed resets.
  // selectedIds is always PHOTO ids (BulkBar acts on photos); in grouped mode
  // selectedSightingIds marks the tiles, and toggling a tile toggles all of
  // its member photo ids (fetched once into membersCache).
  const [selecting, setSelecting] = useState(false)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set())
  const [selectedSightingIds, setSelectedSightingIds] = useState<ReadonlySet<string>>(new Set())
  const membersCache = useRef(new Map<string, Photo[]>())
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
      const f = filtersRef.current
      if (f.flat) {
        setPhotos((prev) => {
          if (prev.some((x) => x.id === p.id)) {
            return prev.map((x) => (x.id === p.id ? p : x))
          }
          // Prepend only when the new photo matches the active filters — and
          // never while anchored in the past: the top of the list isn't "now",
          // the upward pager picks these up on the way back instead.
          if (p.change !== 'new' || !matchesFilters(p, f) || anchoredRef.current) return prev
          return [p, ...prev]
        })
      } else {
        setSightings((prev) => {
          const idx = prev.findIndex((s) => s.id === p.sighting_id)
          if (idx >= 0) {
            const s = prev[idx]
            const fresh = p.change === 'new'
            const updated: Sighting = {
              ...s,
              count: fresh ? s.count + 1 : s.count,
              kept_count: s.kept_count, // keep toggles sync via syncPhoto, not SSE
              started_at: earlierIso(s.started_at, p.captured_at),
              ended_at: laterIso(s.ended_at, p.captured_at),
              last_received_at: laterIso(s.last_received_at, p.received_at),
              // The cover is the earliest frame — a straggler can take it over.
              cover:
                p.id === s.cover.id ||
                Date.parse(p.captured_at) < Date.parse(s.cover.captured_at)
                  ? p
                  : s.cover,
            }
            // Anchored: update in place, no resurfacing — reordering a list
            // whose top isn't "now" would just scramble it.
            if (!fresh || anchoredRef.current) {
              return prev.map((x, i) => (i === idx ? updated : x))
            }
            // Arrival order: a new frame surfaces its whole visit at the top.
            return [updated, ...prev.filter((_, i) => i !== idx)]
          }
          if (p.change !== 'new' || !matchesFilters(p, f) || anchoredRef.current) return prev
          return [
            {
              id: p.sighting_id,
              camera_id: p.camera_id,
              camera_name: p.camera_name,
              site_slug: p.site_slug,
              count: 1,
              kept_count: p.keep ? 1 : 0,
              started_at: p.captured_at,
              ended_at: p.captured_at,
              last_received_at: p.received_at,
              cover: p,
            },
            ...prev,
          ]
        })
        // Keep an open detail pager in step with its arriving burst.
        setSightingPhotos((prev) => {
          if (!prev || prev.sid !== p.sighting_id) return prev
          const known = prev.photos.some((x) => x.id === p.id)
          const next = known
            ? prev.photos.map((x) => (x.id === p.id ? p : x))
            : [...prev.photos, p].sort(byCapture)
          return { ...prev, photos: next }
        })
      }
      setFetchedPhoto((prev) => (prev?.id === p.id ? p : prev))
    })
    const offRemoved = live.subscribe('photo_removed', (d) => {
      const { id, sighting_id } = d as { id: string; sighting_id?: string }
      // Our own delete already updated every store — the SSE echo must not
      // decrement the sighting a second time.
      if (locallyRemoved.current.delete(id)) return
      setPhotos((prev) => prev.filter((p) => p.id !== id))
      if (!sighting_id) return
      // If the removed frame was a tile's cover, refetch the members to crown
      // the next-earliest (the tile would otherwise show a dead image).
      const shown = sightingsRef.current.find((s) => s.id === sighting_id)
      if (shown && shown.cover.id === id && shown.count > 1) {
        void api.getSightingPhotos(sighting_id).then((members) => {
          if (members.length === 0) return
          setSightings((prev) =>
            prev.map((s) => (s.id === sighting_id ? { ...s, cover: members[0] } : s)),
          )
        }).catch(() => undefined)
      }
      setSightings((prev) =>
        prev.flatMap((s) => {
          if (s.id !== sighting_id) return [s]
          if (s.count <= 1) return []
          return [{ ...s, count: s.count - 1, kept_count: Math.min(s.kept_count, s.count - 1) }]
        }),
      )
      setSightingPhotos((prev) =>
        prev && prev.sid === sighting_id
          ? { ...prev, photos: prev.photos.filter((p) => p.id !== id) }
          : prev,
      )
    })
    const offMerge = live.subscribe('sighting_merge', (d) => {
      // A straggler proved two tiles were one visit — fold `from` into `into`.
      const { into, from } = d as { into: string; from: string }
      setSightings((prev) => {
        const a = prev.find((s) => s.id === into)
        const b = prev.find((s) => s.id === from)
        if (!b) return prev
        if (!a) return prev.map((s) => (s.id === from ? { ...s, id: into } : s))
        const merged: Sighting = {
          ...a,
          count: a.count + b.count,
          kept_count: a.kept_count + b.kept_count,
          started_at: earlierIso(a.started_at, b.started_at),
          ended_at: laterIso(a.ended_at, b.ended_at),
          last_received_at: laterIso(a.last_received_at, b.last_received_at),
          cover:
            Date.parse(a.cover.captured_at) <= Date.parse(b.cover.captured_at)
              ? a.cover
              : b.cover,
        }
        return prev.map((s) => (s.id === into ? merged : s)).filter((s) => s.id !== from)
      })
      setSightingPhotos((prev) => (prev && prev.sid === from ? { ...prev, sid: into } : prev))
    })
    return () => {
      offPhoto()
      offRemoved()
      offMerge()
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
        feed.upCursor = null
        feed.busy = false
        feed.busyUp = false
      }
      if (feed.busy) return
      const gen = feed.gen
      feed.busy = true
      setLoading(true)
      setFeedError(null)
      if (reset) {
        setPhotos([])
        setSightings([])
        setHasMore(true)
        // An anchor means there is stream above the first page by definition;
        // the first upward fetch finding nothing corrects this to false.
        setHasMoreUp(at !== null)
        setLoadingUp(false)
        setSelectedIds(new Set())
        setSelectedSightingIds(new Set())
        membersCache.current.clear()
      }
      try {
        // The timeline's brushed range wins over the legacy single-day param.
        const bounds = date ? easternDayBounds(date) : null
        const query = {
          site: site ?? undefined,
          cameraId: cameraId ?? undefined,
          kept: keptOnly || undefined,
          tag: tag ?? undefined,
          capturedAfter: from ?? bounds?.start,
          capturedBefore: to ?? bounds?.end,
          before: feed.cursor ?? undefined,
          anchor: at ?? undefined,
          limit: PAGE_SIZE,
        }
        if (flat) {
          const page = await api.getPhotos(query)
          if (feedRef.current.gen !== gen) return
          feed.cursor = page.next_cursor
          setPhotos((prev) => (reset ? page.items : [...prev, ...page.items]))
          setHasMore(page.next_cursor !== null)
        } else {
          const page = await api.getSightings(query)
          if (feedRef.current.gen !== gen) return
          feed.cursor = page.next_cursor
          // Dedupe on append: an SSE frame may have already moved one of this
          // page's sightings to the top of the list.
          setSightings((prev) => {
            if (reset) return page.items
            const seen = new Set(prev.map((s) => s.id))
            return [...prev, ...page.items.filter((s) => !seen.has(s.id))]
          })
          setHasMore(page.next_cursor !== null)
        }
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
    [site, cameraId, keptOnly, date, tag, flat, from, to, at, handleUnauthorized],
  )

  // Upward pager for the anchored feed: walks from the anchor back toward now,
  // prepending. usePrependCompensation in the grids keeps the viewport pinned.
  const loadUp = useCallback(async () => {
    const feed = feedRef.current
    if (feed.busyUp || at === null) return
    const gen = feed.gen
    feed.busyUp = true
    setLoadingUp(true)
    try {
      const bounds = date ? easternDayBounds(date) : null
      const query = {
        site: site ?? undefined,
        cameraId: cameraId ?? undefined,
        kept: keptOnly || undefined,
        tag: tag ?? undefined,
        capturedAfter: from ?? bounds?.start,
        capturedBefore: to ?? bounds?.end,
        after: feed.upCursor ?? undefined,
        anchor: feed.upCursor ? undefined : at,
        direction: 'newer' as const,
        limit: PAGE_SIZE,
      }
      if (flat) {
        const page = await api.getPhotos(query)
        if (feedRef.current.gen !== gen) return
        feed.upCursor = page.next_cursor
        setPhotos((prev) => {
          const seen = new Set(prev.map((p) => p.id))
          return [...page.items.filter((p) => !seen.has(p.id)), ...prev]
        })
        setHasMoreUp(page.next_cursor !== null)
      } else {
        const page = await api.getSightings(query)
        if (feedRef.current.gen !== gen) return
        feed.upCursor = page.next_cursor
        setSightings((prev) => {
          const seen = new Set(prev.map((s) => s.id))
          return [...page.items.filter((s) => !seen.has(s.id)), ...prev]
        })
        setHasMoreUp(page.next_cursor !== null)
      }
    } catch (err: unknown) {
      if (feedRef.current.gen !== gen) return
      if (err instanceof UnauthorizedError) handleUnauthorized()
      // Other failures: the top sentinel retries on the next intersection.
    } finally {
      if (feedRef.current.gen === gen) {
        feed.busyUp = false
        setLoadingUp(false)
      }
    }
  }, [site, cameraId, keptOnly, date, tag, flat, from, to, at, handleUnauthorized])

  useEffect(() => {
    if (auth.status !== 'ok') return
    void loadPhotos(true)
  }, [auth.status, loadPhotos])

  // --- Activity timeline ----------------------------------------------------
  //
  // Hourly capture counts under the non-time filters; the brush selects a
  // range WITHIN this distribution, so date/from/to deliberately don't
  // refetch it (the shading shows the selection instead).
  const [histogram, setHistogram] = useState<HistogramBucket[]>([])

  useEffect(() => {
    if (auth.status !== 'ok') return
    let cancelled = false
    api
      .getPhotosHistogram({
        site: site ?? undefined,
        cameraId: cameraId ?? undefined,
        kept: keptOnly || undefined,
        tag: tag ?? undefined,
      })
      .then((buckets) => {
        if (!cancelled) setHistogram(buckets)
      })
      .catch((err: unknown) => {
        if (err instanceof UnauthorizedError) handleUnauthorized()
        // Non-auth failures: the strip just doesn't render — the feed works.
      })
    return () => {
      cancelled = true
    }
  }, [auth.status, site, cameraId, keptOnly, tag, handleUnauthorized])

  const loadMore = useCallback(() => {
    if (!loading && hasMore && !feedError) void loadPhotos(false)
  }, [loading, hasMore, feedError, loadPhotos])

  const retry = useCallback(() => {
    void loadPhotos(false)
  }, [loadPhotos])

  // --- Filter handlers (replace, not push; a feed URL clears an open overlay)

  // Filter changes also drop the scrubber anchor (at: null) — a position in
  // one stream is meaningless in another. The flat/grouped toggle keeps it:
  // both modes page from the same arrival instants.
  const handleSiteChange = useCallback(
    (slug: string | null) => {
      // Camera list is site-scoped; a stale id would 404-filter, so clear it.
      navigate(feedUrl({ ...filters, site: slug, cameraId: null, at: null }), { replace: true })
    },
    [filters],
  )

  const handleCameraChange = useCallback(
    (id: string | null) => {
      navigate(feedUrl({ ...filters, cameraId: id, at: null }), { replace: true })
    },
    [filters],
  )

  const handleKeptOnlyChange = useCallback(
    (value: boolean) => {
      navigate(feedUrl({ ...filters, keptOnly: value, at: null }), { replace: true })
    },
    [filters],
  )

  const handleTagChange = useCallback(
    (value: string | null) => {
      navigate(feedUrl({ ...filters, tag: value, at: null }), { replace: true })
    },
    [filters],
  )

  const handleFlatChange = useCallback(
    (value: boolean) => {
      navigate(feedUrl({ ...filters, flat: value }), { replace: true })
    },
    [filters],
  )

  // Timeline brush — a range replaces the legacy single-day param outright.
  const handleRangeChange = useCallback(
    (rangeFrom: string | null, rangeTo: string | null) => {
      navigate(feedUrl({ ...filters, date: null, from: rangeFrom, to: rangeTo, at: null }), {
        replace: true,
      })
    },
    [filters],
  )

  // Scrubber jump: anchor the feed at an instant (replace — dragging around
  // must not pile up history entries). Jumping scrolls to the top, where the
  // anchor page starts.
  const handleJump = useCallback(
    (iso: string) => {
      navigate(feedUrl({ ...filters, at: iso }), { replace: true })
      window.scrollTo(0, 0)
    },
    [filters],
  )

  const jumpToLatest = useCallback(() => {
    navigate(feedUrl({ ...filters, at: null }), { replace: true })
    window.scrollTo(0, 0)
  }, [filters])

  const handleViewChange = useCallback((next: View) => {
    const to =
      next === 'photos'
        ? '/'
        : next === 'nodes'
          ? nodesUrl()
          : next === 'survey'
            ? surveyUrl(EMPTY_SURVEY)
            : settingsUrl()
    if (location.pathname + location.search === to) return
    navigate(to)
  }, [])

  // --- Detail overlay -----------------------------------------------------

  // The URL id may be a generous prefix ref (ids.ts), so match accordingly.
  // Flat mode pages through the whole feed; grouped mode pages within the
  // open sighting's members (fetched into sightingPhotos).
  const photoId = view === 'photos' ? route.photoId : null
  const pagingList = useMemo(
    () => (flat ? photos : (sightingPhotos?.photos ?? [])),
    [flat, photos, sightingPhotos],
  )
  const selectedIndex =
    photoId === null ? -1 : pagingList.findIndex((p) => idMatchesRef(p.id, photoId))
  const inFeed = selectedIndex >= 0
  // Grouped: a tapped tile's cover stands in while its members load, so the
  // overlay opens instantly.
  const coverMatch =
    !flat && photoId !== null
      ? (sightings.find((s) => idMatchesRef(s.cover.id, photoId))?.cover ?? null)
      : null
  const selected = inFeed
    ? pagingList[selectedIndex]
    : fetchedPhoto !== null && idMatchesRef(fetchedPhoto.id, photoId ?? '')
      ? fetchedPhoto
      : coverMatch

  // Deep link: the routed photo isn't on hand — fetch it individually so the
  // overlay shows right away. A prefix ref is canonicalized to the full id
  // once resolved. On 404 (or any non-auth failure) silently replace-navigate
  // back to the feed.
  useEffect(() => {
    if (auth.status !== 'ok' || photoId === null || inFeed || coverMatch !== null) return
    if (fetchedPhoto !== null && idMatchesRef(fetchedPhoto.id, photoId)) return
    let cancelled = false
    api
      .getPhoto(photoId)
      .then((photo) => {
        if (cancelled) return
        setFetchedPhoto(photo)
        if (photo.id !== photoId) {
          navigate(photoUrl(photo.id, filters), { replace: true })
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof UnauthorizedError) {
          handleUnauthorized()
          return
        }
        navigate(feedUrl(filters), { replace: true })
      })
    return () => {
      cancelled = true
    }
  }, [auth.status, photoId, inFeed, coverMatch, fetchedPhoto, filters, handleUnauthorized])

  // Grouped: fetch the open photo's whole sighting so prev/next page the
  // burst. The anchor carrying the sighting id is the tapped cover or the
  // deep-link fetch above, whichever exists.
  useEffect(() => {
    if (auth.status !== 'ok' || flat || photoId === null) return
    if (sightingPhotos?.photos.some((p) => idMatchesRef(p.id, photoId))) return
    const anchor =
      coverMatch ??
      (fetchedPhoto !== null && idMatchesRef(fetchedPhoto.id, photoId) ? fetchedPhoto : null)
    if (anchor === null) return
    let cancelled = false
    api
      .getSightingPhotos(anchor.sighting_id)
      .then((members) => {
        if (!cancelled) setSightingPhotos({ sid: anchor.sighting_id, photos: members })
      })
      .catch((err: unknown) => {
        if (!cancelled && err instanceof UnauthorizedError) handleUnauthorized()
      })
    return () => {
      cancelled = true
    }
  }, [auth.status, flat, photoId, sightingPhotos, coverMatch, fetchedPhoto, handleUnauthorized])

  // Closing the overlay drops the member list (reopening refetches — cheap,
  // and never stale).
  useEffect(() => {
    if (photoId === null) setSightingPhotos(null)
  }, [photoId])

  // Where the open photo's sighting sits in the grouped feed — the pivot for
  // paging the overlay ACROSS sightings, not just within one burst.
  const sightingIndex = useMemo(() => {
    if (flat || !selected) return -1
    return sightings.findIndex((s) => s.id === selected.sighting_id)
  }, [flat, selected, sightings])

  // Prefetch the next page while paging through the overlay near the end —
  // by photo in flat mode, by sighting in grouped mode.
  useEffect(() => {
    if (flat) {
      if (selectedIndex < 0) return
      if (selectedIndex >= photos.length - PREFETCH_MARGIN && hasMore && !loading) {
        void loadPhotos(false)
      }
      return
    }
    if (sightingIndex < 0) return
    if (sightingIndex >= sightings.length - PREFETCH_MARGIN && hasMore && !loading) {
      void loadPhotos(false)
    }
  }, [
    flat,
    selectedIndex,
    photos.length,
    sightingIndex,
    sightings.length,
    hasMore,
    loading,
    loadPhotos,
  ])

  // Arrow past a burst's edge: continue into the neighboring sighting.
  // Forward lands on its first frame (the cover); backward fetches the
  // members first so it can land on the LAST frame, the way stepping
  // backward through a stream should.
  const openAdjacentSighting = useCallback(
    async (dir: -1 | 1) => {
      if (sightingIndex < 0) return
      const target = sightings[sightingIndex + dir]
      if (!target) return
      if (dir === 1) {
        navigate(photoUrl(target.cover.id, filters), { replace: true })
        return
      }
      try {
        const members = await api.getSightingPhotos(target.id)
        if (members.length === 0) return
        setSightingPhotos({ sid: target.id, photos: members })
        navigate(photoUrl(members[members.length - 1].id, filters), { replace: true })
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
      }
    },
    [sightingIndex, sightings, filters, handleUnauthorized],
  )

  const openPhoto = useCallback(
    (id: string) => {
      navigate(photoUrl(id, filters))
    },
    [filters],
  )

  const openSighting = useCallback(
    (s: Sighting) => {
      // Open on the cover; the member-fetch effect wires up prev/next.
      navigate(photoUrl(s.cover.id, filters))
    },
    [filters],
  )

  // Prev/next replace the current entry — paging through 50 photos must not
  // create 50 history entries.
  const showPrev = useCallback(() => {
    if (selectedIndex > 0) {
      navigate(photoUrl(pagingList[selectedIndex - 1].id, filters), { replace: true })
    } else if (!flat) {
      void openAdjacentSighting(-1)
    }
  }, [selectedIndex, pagingList, filters, flat, openAdjacentSighting])

  const showNext = useCallback(() => {
    if (selectedIndex >= 0 && selectedIndex < pagingList.length - 1) {
      navigate(photoUrl(pagingList[selectedIndex + 1].id, filters), { replace: true })
    } else if (!flat) {
      void openAdjacentSighting(1)
    }
  }, [selectedIndex, pagingList, filters, flat, openAdjacentSighting])

  const closeDetail = useCallback(() => {
    closeOverlay(feedUrl(filters))
  }, [filters])

  // One photo changed — sync every store that may hold it: the flat feed,
  // the deep-link slot, the open sighting's members, and the sighting tile
  // (cover swap + kept badge; keptDelta is ±1 on a keep toggle).
  const syncPhoto = useCallback((updated: Photo, keptDelta = 0) => {
    setPhotos((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
    setFetchedPhoto((prev) => (prev?.id === updated.id ? updated : prev))
    setSightingPhotos((prev) =>
      prev && prev.sid === updated.sighting_id
        ? { ...prev, photos: prev.photos.map((p) => (p.id === updated.id ? updated : p)) }
        : prev,
    )
    setSightings((prev) =>
      prev.map((s) =>
        s.id === updated.sighting_id
          ? {
              ...s,
              kept_count: Math.max(0, Math.min(s.count, s.kept_count + keptDelta)),
              cover: s.cover.id === updated.id ? updated : s.cover,
            }
          : s,
      ),
    )
  }, [])

  const toggleKeep = useCallback(
    async (photo: Photo) => {
      try {
        const updated = await api.setKeep(photo.id, !photo.keep)
        syncPhoto(updated, (updated.keep ? 1 : 0) - (photo.keep ? 1 : 0))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
        else throw err
      }
    },
    [syncPhoto, handleUnauthorized],
  )

  const requestFull = useCallback(
    async (photo: Photo, quality: 'standard' | 'max') => {
      try {
        syncPhoto(await api.requestFull(photo.id, quality))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
        else throw err
      }
    },
    [syncPhoto, handleUnauthorized],
  )

  // Tag mutations sync the photo stores and refresh the tag filter counts.
  const applyPhotoUpdate = useCallback(
    (updated: Photo) => {
      syncPhoto(updated)
      setTagsVersion((v) => v + 1)
    },
    [syncPhoto],
  )

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

  // Re-fetch one photo (e.g. after its full-res arrives) and sync the stores.
  const refreshPhoto = useCallback(
    async (photo: Photo) => {
      try {
        syncPhoto(await api.getPhoto(photo.id))
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) handleUnauthorized()
      }
    },
    [syncPhoto, handleUnauthorized],
  )

  // Ids this tab deleted itself — their echo comes back over SSE, and the
  // grouped handler's count decrement must not run twice.
  const locallyRemoved = useRef(new Set<string>())

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
      locallyRemoved.current.add(photo.id)
      setFetchedPhoto((prev) => (prev?.id === photo.id ? null : prev))
      if (flat) {
        const idx = photos.findIndex((p) => p.id === photo.id)
        const remaining = photos.filter((p) => p.id !== photo.id)
        setPhotos(remaining)
        // Move (replace) to the adjacent photo's URL, or back to the feed when
        // none remain (or the deleted photo was a deep link outside the feed).
        if (idx < 0 || remaining.length === 0) {
          navigate(feedUrl(filters), { replace: true })
        } else {
          const nextIdx = Math.min(Math.max(idx, 0), remaining.length - 1)
          navigate(photoUrl(remaining[nextIdx].id, filters), { replace: true })
        }
        return
      }
      // Grouped: shrink the member list and the tile; the tile disappears
      // with its last frame.
      const members = sightingPhotos?.sid === photo.sighting_id ? sightingPhotos.photos : []
      const idx = members.findIndex((p) => p.id === photo.id)
      const remaining = members.filter((p) => p.id !== photo.id)
      setSightingPhotos(
        remaining.length > 0 ? { sid: photo.sighting_id, photos: remaining } : null,
      )
      setSightings((prev) =>
        prev.flatMap((s) => {
          if (s.id !== photo.sighting_id) return [s]
          if (s.count <= 1 || remaining.length === 0) return []
          return [
            {
              ...s,
              count: s.count - 1,
              kept_count: Math.max(0, s.kept_count - (photo.keep ? 1 : 0)),
              cover: s.cover.id === photo.id ? remaining[0] : s.cover,
            },
          ]
        }),
      )
      if (idx < 0 || remaining.length === 0) {
        navigate(feedUrl(filters), { replace: true })
      } else {
        const nextIdx = Math.min(Math.max(idx, 0), remaining.length - 1)
        navigate(photoUrl(remaining[nextIdx].id, filters), { replace: true })
      }
    },
    [photos, sightingPhotos, flat, filters, handleUnauthorized],
  )

  // --- Bulk selection -------------------------------------------------------

  const toggleSelecting = useCallback(() => {
    setSelecting((prev) => !prev)
    setSelectedIds(new Set())
    setSelectedSightingIds(new Set())
  }, [])

  const toggleSelected = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Selecting a sighting tile selects the photos it REPRESENTS: its members
  // under the active filters (a kept-filtered tile must never smuggle the
  // rest of its burst into a bulk delete). Members are fetched once per tile.
  const toggleSelectedSighting = useCallback(
    async (s: Sighting) => {
      let members = membersCache.current.get(s.id)
      if (!members) {
        try {
          members = (await api.getSightingPhotos(s.id)).filter((p) =>
            matchesFilters(p, filtersRef.current),
          )
        } catch (err: unknown) {
          if (err instanceof UnauthorizedError) handleUnauthorized()
          return
        }
        membersCache.current.set(s.id, members)
      }
      const ids = members.map((p) => p.id)
      const on = !selectedSightingIds.has(s.id)
      setSelectedSightingIds((prev) => {
        const next = new Set(prev)
        if (on) next.add(s.id)
        else next.delete(s.id)
        return next
      })
      setSelectedIds((prev) => {
        const next = new Set(prev)
        for (const id of ids) {
          if (on) next.add(id)
          else next.delete(id)
        }
        return next
      })
    },
    [selectedSightingIds, handleUnauthorized],
  )

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
    // Grouped: kept counts moved on an unknown number of tiles — refetch.
    if (!filtersRef.current.flat) void loadPhotos(true)
    if (!failed) {
      setSelecting(false)
      setSelectedIds(new Set())
      setSelectedSightingIds(new Set())
    }
    setBulkBusy(false)
  }, [forSelected, loadPhotos, handleUnauthorized])

  const bulkDelete = useCallback(async () => {
    setBulkBusy(true)
    const deleted = new Set<string>()
    let failed = false
    try {
      await forSelected(async (id) => {
        await api.deletePhoto(id)
        locallyRemoved.current.add(id)
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
    // Grouped: counts/covers shifted across tiles — refetch (also clears
    // selection state, including the sighting marks).
    if (!filtersRef.current.flat) void loadPhotos(true)
    if (!failed) setSelecting(false)
    setBulkBusy(false)
  }, [forSelected, loadPhotos, handleUnauthorized])

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
    } else if (view === 'survey') {
      document.title = `${TITLE_BASE} · Survey`
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
        tags={tags}
        tag={tag}
        onTagChange={handleTagChange}
        flat={flat}
        onFlatChange={handleFlatChange}
        selecting={selecting}
        onToggleSelecting={toggleSelecting}
        onSignOut={() => void api.signOut()}
      />
      {view === 'photos' && histogram.length > 0 && (
        <PhotoTimeline
          buckets={histogram}
          from={from}
          to={to}
          onBrush={handleRangeChange}
          onClear={() => handleRangeChange(null, null)}
        />
      )}
      {/* Mounted even under the detail overlay (which covers it at z 100):
          unmounting would flap the hidden-native-scrollbar state and reflow
          the feed behind the overlay on every open/close. */}
      {view === 'photos' && <TimeScrubber buckets={histogram} onJump={handleJump} />}
      {view === 'photos' && at !== null && hasMoreUp && (
        <button type="button" className="btn jump-latest" onClick={jumpToLatest}>
          <ChevronsUp size={14} aria-hidden="true" />
          Jump to latest
        </button>
      )}
      {view === 'photos' ? (
        flat ? (
          <PhotoGrid
            photos={photos}
            loading={loading}
            hasMore={hasMore}
            hasMoreUp={hasMoreUp}
            loadingUp={loadingUp}
            error={feedError}
            onLoadMore={loadMore}
            onLoadMoreUp={() => void loadUp()}
            onRetry={retry}
            onSelect={openPhoto}
            selecting={selecting}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelected}
          />
        ) : (
          <SightingGrid
            sightings={sightings}
            loading={loading}
            hasMore={hasMore}
            hasMoreUp={hasMoreUp}
            loadingUp={loadingUp}
            error={feedError}
            onLoadMore={loadMore}
            onLoadMoreUp={() => void loadUp()}
            onRetry={retry}
            onSelect={openSighting}
            selecting={selecting}
            selectedIds={selectedSightingIds}
            onToggleSelect={(s) => void toggleSelectedSighting(s)}
          />
        )
      ) : view === 'nodes' ? (
        <NodesView
          sites={sites}
          nodeId={route.nodeId}
          demo={demo}
          onUnauthorized={handleUnauthorized}
        />
      ) : view === 'survey' ? (
        <SurveyView
          me={auth.me}
          demo={demo}
          query={route.survey}
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
          hasPrev={selectedIndex > 0 || (!flat && sightingIndex > 0)}
          hasNext={
            (inFeed && selectedIndex < pagingList.length - 1) ||
            (!flat && sightingIndex >= 0 && sightingIndex < sightings.length - 1)
          }
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
