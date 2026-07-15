import type {
  MeshEvent,
  Camera,
  DeviceToken,
  DeviceTokenCreated,
  FullRequest,
  Me,
  NodeCommand,
  NodeCommandKind,
  NodeHealth,
  Photo,
  PhotosPage,
  Probe,
  ProbeSession,
  RetentionStats,
  Site,
  TagCount,
  TelemetrySeries,
} from './types'

/** Thrown for any 401 — the caller should fall back to the sign-in splash. */
export class UnauthorizedError extends Error {
  constructor() {
    super('Not signed in')
    this.name = 'UnauthorizedError'
  }
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { credentials: 'same-origin', ...init })
  } catch {
    throw new ApiError(0, 'Network error, check your connection')
  }
  if (res.status === 401) {
    throw new UnauthorizedError()
  }
  if (!res.ok) {
    // surface the server's human-readable detail when there is one (e.g. the
    // demo's "mesh is busy" 429) instead of a bare status code
    let message = `Request failed (${res.status})`
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body.detail === 'string' && body.detail) message = body.detail
    } catch {
      // not JSON — keep the generic message
    }
    throw new ApiError(res.status, message)
  }
  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

export function getMe(): Promise<Me> {
  return request<Me>('/api/v1/me')
}

export function getSites(includeHidden = false): Promise<Site[]> {
  return request<Site[]>(`/api/v1/sites${includeHidden ? '?include_hidden=true' : ''}`)
}

export function getCameras(site?: string, includeHidden = false): Promise<Camera[]> {
  const params = new URLSearchParams()
  if (site) params.set('site', site)
  if (includeHidden) params.set('include_hidden', 'true')
  const qs = params.toString()
  return request<Camera[]>(`/api/v1/cameras${qs ? `?${qs}` : ''}`)
}

export interface SitePatch {
  name?: string
  hidden?: boolean
}

export function patchSite(id: string, patch: SitePatch): Promise<Site> {
  return request<Site>(`/api/v1/sites/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

export interface CameraPatch {
  name?: string
  notes?: string
  hidden?: boolean
  /** Set together — the survey map's gateway pin-drop. */
  lat?: number
  lon?: number
}

export function patchCamera(id: string, patch: CameraPatch): Promise<Camera> {
  return request<Camera>(`/api/v1/cameras/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

export function getDeviceTokens(): Promise<DeviceToken[]> {
  return request<DeviceToken[]>('/api/v1/device-tokens')
}

export function createDeviceToken(name: string): Promise<DeviceTokenCreated> {
  return request<DeviceTokenCreated>('/api/v1/device-tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export function revokeDeviceToken(id: string): Promise<void> {
  return request<void>(`/api/v1/device-tokens/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export function getRetentionStats(): Promise<RetentionStats> {
  return request<RetentionStats>('/api/v1/retention')
}

export interface PhotoQuery {
  site?: string
  cameraId?: string
  kept?: boolean
  tag?: string
  capturedAfter?: string
  capturedBefore?: string
  before?: string
  limit?: number
}

export function getPhotos(query: PhotoQuery): Promise<PhotosPage> {
  const params = new URLSearchParams()
  if (query.site) params.set('site', query.site)
  if (query.cameraId != null) params.set('camera_id', query.cameraId)
  if (query.kept) params.set('kept', 'true')
  if (query.tag) params.set('tag', query.tag)
  if (query.capturedAfter) params.set('captured_after', query.capturedAfter)
  if (query.capturedBefore) params.set('captured_before', query.capturedBefore)
  if (query.before) params.set('before', query.before)
  params.set('limit', String(query.limit ?? 50))
  return request<PhotosPage>(`/api/v1/photos?${params.toString()}`)
}

/** Fetch a single photo by id — used for deep links before the feed loads. */
export function getPhoto(id: string): Promise<Photo> {
  return request<Photo>(`/api/v1/photos/${encodeURIComponent(id)}`)
}

export function setKeep(id: string, keep: boolean): Promise<Photo> {
  return request<Photo>(`/api/v1/photos/${encodeURIComponent(id)}/keep`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep }),
  })
}

export async function getFullRequest(id: string): Promise<FullRequest | null> {
  try {
    return await request<FullRequest>(`/api/v1/photos/${encodeURIComponent(id)}/full-request`)
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export function requestFull(
  id: string,
  quality: 'standard' | 'max' = 'standard',
): Promise<Photo> {
  return request<Photo>(`/api/v1/photos/${encodeURIComponent(id)}/request-full`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ quality }),
  })
}

export function getTags(): Promise<TagCount[]> {
  return request<TagCount[]>('/api/v1/tags')
}

export function addPhotoTag(id: string, name: string): Promise<Photo> {
  return request<Photo>(`/api/v1/photos/${encodeURIComponent(id)}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export function removePhotoTag(id: string, slug: string): Promise<Photo> {
  return request<Photo>(
    `/api/v1/photos/${encodeURIComponent(id)}/tags/${encodeURIComponent(slug)}`,
    { method: 'DELETE' },
  )
}

export function deletePhoto(id: string): Promise<void> {
  return request<void>(`/api/v1/photos/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export function getMeshRecent(limit = 80): Promise<MeshEvent[]> {
  return request<MeshEvent[]>(`/api/v1/mesh/recent?limit=${limit}`)
}

export function getNodesHealth(): Promise<NodeHealth[]> {
  return request<NodeHealth[]>('/api/v1/nodes/health')
}

export function getNodeCommands(id: string): Promise<NodeCommand[]> {
  return request<NodeCommand[]>(`/api/v1/nodes/${encodeURIComponent(id)}/commands`)
}

export function queueNodeCommand(
  id: string,
  kind: NodeCommandKind,
  payload?: Record<string, unknown>,
): Promise<void> {
  return request<void>(`/api/v1/nodes/${encodeURIComponent(id)}/commands`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, payload }),
  })
}

export interface ProbeQuery {
  node?: string
  from?: string
  to?: string
  /** 'all' includes no-fix probes (the survey view wants them for the count
   *  + side list); the server default 'only' hides them. */
  fix?: 'only' | 'all'
}

export function getProbes(query: ProbeQuery = {}): Promise<Probe[]> {
  const params = new URLSearchParams()
  if (query.node) params.set('node', query.node)
  if (query.from) params.set('from', query.from)
  if (query.to) params.set('to', query.to)
  if (query.fix) params.set('fix', query.fix)
  const qs = params.toString()
  return request<Probe[]>(`/api/v1/probes${qs ? `?${qs}` : ''}`)
}

export function getProbeSessions(node?: string, gapMin?: number): Promise<ProbeSession[]> {
  const params = new URLSearchParams()
  if (node) params.set('node', node)
  if (gapMin != null) params.set('gap_min', String(gapMin))
  const qs = params.toString()
  return request<ProbeSession[]>(`/api/v1/probes/sessions${qs ? `?${qs}` : ''}`)
}

export function getNodeTelemetry(id: string, hours: number): Promise<TelemetrySeries> {
  return request<TelemetrySeries>(
    `/api/v1/nodes/${encodeURIComponent(id)}/telemetry?hours=${hours}`,
  )
}

export function photoImageUrl(id: string, size: 'thumb' | 'full', version?: number | null): string {
  // version (byte size) busts the immutable browser cache when a stored image
  // is overwritten in place — e.g. a max-quality full replacing the standard one.
  const v = version != null ? `&v=${version}` : ''
  return `/api/v1/photos/${encodeURIComponent(id)}/image?size=${size}${v}`
}

export function signIn(): void {
  window.location.href = '/auth/login?next=' + encodeURIComponent(location.pathname)
}

export async function signOut(): Promise<void> {
  try {
    await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' })
  } finally {
    location.reload()
  }
}
