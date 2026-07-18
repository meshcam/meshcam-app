// Live updates for the whole tab — and across tabs.
//
// The Gateway speaks HTTP/1.1 (no ALPN), and browsers cap ~6 connections per
// origin ACROSS ALL TABS. Naive one-EventSource-per-tab therefore starves the
// connection pool once a few tabs are open (symptom: requests queue forever).
// So: Web Locks leader election — exactly ONE tab holds the real SSE
// connection and rebroadcasts every event to the others via BroadcastChannel.
// When the leader tab closes, the lock passes and the next tab takes over.
// Browsers without Web Locks/BroadcastChannel fall back to per-tab streams.

type Handler = (data: unknown) => void

/** Every event type the server emits — the leader must relay all of them,
 * not just the ones this particular tab has local subscribers for. */
const EVENT_TYPES = [
  'photo',
  'photo_removed',
  'sighting_merge',
  'request',
  'node',
  'mesh',
] as const

const CHANNEL = 'trailcam-live'
const LEADER_LOCK = 'trailcam-sse-leader'

class Live {
  private es: EventSource | null = null
  private bc: BroadcastChannel | null = null
  private handlers = new Map<string, Set<Handler>>()
  private statusSubs = new Set<(up: boolean) => void>()
  private started = false
  private leader = false
  private releaseLock: (() => void) | null = null
  connected = false

  connect(): void {
    if (this.started) return
    this.started = true
    const canShare =
      typeof BroadcastChannel !== 'undefined' &&
      typeof navigator !== 'undefined' &&
      'locks' in navigator
    if (!canShare) {
      this.openSource()
      return
    }
    this.bc = new BroadcastChannel(CHANNEL)
    this.bc.onmessage = (e: MessageEvent) => {
      if (this.leader) return // own rebroadcasts
      const { type, data } = e.data as { type: string; data: unknown }
      if (type === '$status') this.setStatus(Boolean(data))
      else this.dispatch(type, data)
    }
    // Follower until the lock says otherwise; assume the leader is up (it
    // corrects us via $status broadcasts either way).
    this.setStatus(true)
    void navigator.locks.request(LEADER_LOCK, () => {
      this.leader = true
      this.openSource()
      // Hold the lock until this tab closes (or disconnect() releases it).
      return new Promise<void>((resolve) => {
        this.releaseLock = resolve
      })
    })
  }

  disconnect(): void {
    this.es?.close()
    this.es = null
    this.bc?.close()
    this.bc = null
    this.releaseLock?.() // hand leadership to the next tab
    this.releaseLock = null
    this.leader = false
    this.started = false
    this.setStatus(false)
  }

  /** Subscribe to a named SSE event; returns an unsubscribe function. */
  subscribe(type: string, handler: Handler): () => void {
    let set = this.handlers.get(type)
    if (!set) {
      set = new Set()
      this.handlers.set(type, set)
    }
    set.add(handler)
    return () => {
      set.delete(handler)
    }
  }

  onStatus(handler: (up: boolean) => void): () => void {
    this.statusSubs.add(handler)
    handler(this.connected)
    return () => {
      this.statusSubs.delete(handler)
    }
  }

  private openSource(): void {
    if (this.es) return
    this.es = new EventSource('/api/v1/events')
    this.es.onopen = () => {
      this.setStatus(true)
      this.bc?.postMessage({ type: '$status', data: true })
    }
    // EventSource auto-reconnects; status flips let poll fallbacks engage.
    this.es.onerror = () => {
      this.setStatus(false)
      this.bc?.postMessage({ type: '$status', data: false })
    }
    for (const type of EVENT_TYPES) {
      this.es.addEventListener(type, (e: MessageEvent) => {
        let data: unknown
        try {
          data = JSON.parse(e.data as string)
        } catch {
          return // malformed frame; the next event supersedes it
        }
        this.dispatch(type, data)
        this.bc?.postMessage({ type, data })
      })
    }
  }

  private dispatch(type: string, data: unknown): void {
    this.handlers.get(type)?.forEach((h) => h(data))
  }

  private setStatus(up: boolean): void {
    if (this.connected === up) return
    this.connected = up
    this.statusSubs.forEach((h) => h(up))
  }
}

export const live = new Live()
