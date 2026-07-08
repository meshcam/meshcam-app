// Live mesh traffic — the gateway's view of the radio channel, streamed to the
// browser. Initial fill from /api/v1/mesh/recent, then 'mesh' SSE events prepend.
// Every telemetry beat is one row; the interesting story (announces with signal
// quality, command deliveries, chunked full-res progress, failures) rides in
// `extra` and is rendered as a human line.
import { Radio } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import { timeAgo } from '../format'
import { live } from '../live'
import type { MeshEvent, MeshTransfer } from '../types'

const MAX_ROWS = 120
/** Drop an in-flight pull from the progress strip if no beat lands for this
 * long (chunks arrive every ~30-40 s; 5 min quiet = it died or we missed it). */
const ACTIVE_STALE_MS = 5 * 60_000

function secs(ms: number): string {
  return ms >= 10000 ? `${Math.round(ms / 1000)} s` : `${(ms / 1000).toFixed(1)} s`
}

function tierLabel(quality?: string): string {
  return quality === 'max' ? 'original' : 'HD'
}

/** One in-flight photo pull, tracked from its chunk beats. */
interface ActivePull {
  node: string
  t: MeshTransfer
  at: number // ms epoch of the latest beat
}

/** Rebuild the in-flight set from a newest-first event list (initial fill):
 * the first beat seen per event_id is its latest state; a reassembled beat
 * means that pull already finished. */
function activeFromEvents(events: MeshEvent[]): Record<string, ActivePull> {
  const active: Record<string, ActivePull> = {}
  const settled = new Set<string>()
  for (const e of events) {
    const t = e.extra?.transfer
    if (!t?.event_id || active[t.event_id] || settled.has(t.event_id)) continue
    if (t.reassembled != null) settled.add(t.event_id)
    else if (t.chunk != null) {
      active[t.event_id] = { node: e.node, t, at: new Date(e.at).getTime() }
    }
  }
  return active
}

function describe(e: MeshEvent): string {
  const x = e.extra
  if (x?.transfer) {
    const t = x.transfer
    const took = t.ms != null ? ` in ${secs(t.ms)}` : ''
    const tier = t.quality === 'max' ? 'original' : 'HD'
    if (t.reassembled != null)
      return `${tier} photo reassembled — ${t.event_id} (${(t.reassembled / 1024).toFixed(1)} KB${took})`
    return `${tier} chunk ${t.chunk}/${t.chunks} — ${t.event_id}${took}`
  }
  if (x?.transfer_error) return `transfer error (${x.transfer_error}) — will retry`
  if (x?.rf) {
    const r = x.rf
    const snr = r.snr != null ? ` (snr ${r.snr.toFixed(1)}` : ''
    const head = r.headroom != null ? `, ${r.headroom.toFixed(1)} dB headroom)` : snr ? ')' : ''
    if (r.event === 'grant') return `radio profile → ${r.profile} granted${snr}${head}`
    if (r.event === 'confirmed') return `radio profile ${r.profile} confirmed by leaf${snr}${head}`
    if (r.event === 'revert') return `radio profile reverted to ${r.profile} (no confirm)`
    if (r.event === 'adopted') return `leaf found on ${r.profile} — re-adopted`
    return `radio profile ${r.event}: ${r.profile}`
  }
  if (x?.link === 'up') return 'link established'
  if (x?.status?.startsWith('alert')) return `motion alert (${x.status})`
  if (x?.status === 'checkin') return 'check-in'
  if (x?.status === 'hello') return 'cold boot'
  if (e.kind === 'gateway') return `gateway heartbeat${x?.free_heap ? ` (heap ${Math.round(x.free_heap / 1024)} KB)` : ''}`
  return 'heartbeat'
}

// RNS packet decode for the inspector. Header (HEADER_1): flags, hops, 16-byte
// destination hash, context byte. An announce's data section is fully public by
// design (it's how the mesh learns identities): X25519 + Ed25519 public keys,
// name hash, random hash (5 random bytes + 5-byte emission timestamp — the
// replay-protection clock), Ed25519 signature, then free-form app data.
interface PacketField {
  label: string
  hex: string
  note?: string
}

function decodePacket(hex: string): { type: string; dest: string; fields: PacketField[] } {
  const b0 = Number.parseInt(hex.slice(0, 2), 16)
  const typeBits = b0 & 0x03
  const type =
    typeBits === 1 ? 'announce' : typeBits === 2 ? 'link request' : typeBits === 3 ? 'proof' : 'data'
  const dest = hex.slice(4, 36)
  const fields: PacketField[] = [
    { label: 'flags', hex: hex.slice(0, 2), note: `packet type: ${type}` },
    { label: 'hops', hex: hex.slice(2, 4), note: `${Number.parseInt(hex.slice(2, 4), 16)}` },
    { label: 'destination', hex: dest },
    { label: 'context', hex: hex.slice(36, 38) },
  ]
  // Announce payload: 64B keys + 10B name hash + 10B random hash + 64B signature + app data.
  if (typeBits === 1 && hex.length >= 38 + 296) {
    // Data layout (hex chars): keys 0..128, name hash 128..148, random hash
    // 148..168 (last 5 BYTES = emission clock), signature 168..296, app data 296..
    const d = hex.slice(38)
    const emitted = Number.parseInt(d.slice(158, 168), 16)
    const appHex = d.slice(296)
    let ascii = ''
    for (let i = 0; i + 1 < appHex.length; i += 2) {
      const c = Number.parseInt(appHex.slice(i, i + 2), 16)
      ascii += c >= 32 && c < 127 ? String.fromCharCode(c) : '·'
    }
    fields.push(
      { label: 'X25519 pubkey', hex: d.slice(0, 64), note: 'peers encrypt to the node with this' },
      { label: 'Ed25519 pubkey', hex: d.slice(64, 128), note: 'verifies this node’s signatures' },
      { label: 'name hash', hex: d.slice(128, 148), note: 'hash of the destination name' },
      {
        label: 'random hash',
        hex: d.slice(148, 168),
        note: `5 random bytes + emission clock (${emitted}s) — replay protection`,
      },
      { label: 'signature', hex: d.slice(168, 296), note: 'Ed25519 over all of the above' },
      { label: 'app data', hex: d.slice(296), note: `"${ascii}"` },
    )
  }
  return { type, dest, fields }
}

function signal(e: MeshEvent): string | null {
  if (e.rssi == null) return null
  return `${Math.round(e.rssi)} dBm${e.snr != null ? ` / ${e.snr.toFixed(1)} snr` : ''}`
}

// Detail rows for an expanded transfer beat — the bandwidth story of one chunk (or of
// the whole reassembled file). Rendered with the same field-table styling the packet
// inspector uses.
function transferFields(t: MeshTransfer): { label: string; value: string; note?: string }[] {
  const rows: { label: string; value: string; note?: string }[] = []
  const size = t.reassembled ?? t.bytes
  if (t.reassembled != null) {
    rows.push({ label: 'reassembled', value: `${(t.reassembled / 1024).toFixed(1)} KB`, note: `${t.chunks} chunks` })
  } else if (t.chunk != null) {
    rows.push({
      label: 'chunk',
      value: `${t.chunk} of ${t.chunks}`,
      note:
        t.offset != null && t.total != null
          ? `bytes ${t.offset.toLocaleString()}–${(t.offset + (t.bytes ?? 0)).toLocaleString()} of ${t.total.toLocaleString()}`
          : undefined,
    })
    if (t.bytes != null) rows.push({ label: 'size', value: `${(t.bytes / 1024).toFixed(1)} KB` })
  }
  if (t.ms != null) {
    rows.push({ label: 'took', value: secs(t.ms), note: t.reassembled != null ? 'first chunk start → last chunk done' : 'resource start → complete' })
  }
  if (t.bps != null && size != null) {
    // Goodput vs the profile's raw bitrate = what's left after protocol overhead,
    // turnarounds and retries. Beats from gateway-0.4.0+ carry the actual profile's
    // raw_bps (ADR switches it); older beats were always the sf8/bw125 base (~488).
    const raw = t.raw_bps ?? 488
    rows.push({ label: 'goodput', value: `${t.bps} B/s`, note: `${((t.bps / raw) * 100).toFixed(0)}% of the profile's ~${raw} B/s raw` })
  }
  if (t.quality) {
    rows.push({
      label: 'quality',
      value: t.quality === 'max' ? 'original' : 'HD',
      note: `${t.quality} tier`,
    })
  }
  if (t.profile) rows.push({ label: 'radio', value: t.profile })
  return rows
}

export default function MeshFeed({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [events, setEvents] = useState<MeshEvent[]>([])
  const [active, setActive] = useState<Record<string, ActivePull>>({})
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const seeded = useRef(false)

  useEffect(() => {
    let cancelled = false
    api
      .getMeshRecent()
      .then((data) => {
        if (cancelled) return
        seeded.current = true
        setEvents((prev) => (prev.length ? prev : data))
        setActive((prev) => (Object.keys(prev).length ? prev : activeFromEvents(data)))
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof api.UnauthorizedError) {
          onUnauthorized()
          return
        }
        setError(err instanceof Error ? err.message : 'Failed to load mesh feed')
      })
    return () => {
      cancelled = true
    }
  }, [onUnauthorized])

  useEffect(
    () =>
      live.subscribe('mesh', (data) => {
        const e = data as MeshEvent
        setEvents((prev) => [e, ...prev].slice(0, MAX_ROWS))
        const t = e.extra?.transfer
        if (t?.event_id) {
          setActive((prev) => {
            if (t.reassembled != null) {
              if (!(t.event_id in prev)) return prev
              const next = { ...prev }
              delete next[t.event_id]
              return next
            }
            if (t.chunk == null) return prev
            return { ...prev, [t.event_id]: { node: e.node, t, at: Date.now() } }
          })
        }
      }),
    [],
  )

  // Re-render periodically so ETAs refresh and dead pulls age out of the strip.
  useEffect(() => {
    const id = window.setInterval(
      () =>
        setActive((prev) => {
          const cutoff = Date.now() - ACTIVE_STALE_MS
          const fresh = Object.entries(prev).filter(([, a]) => a.at >= cutoff)
          return fresh.length === Object.keys(prev).length
            ? { ...prev } // same set — new object just to refresh ETA text
            : Object.fromEntries(fresh)
        }),
      10_000,
    )
    return () => window.clearInterval(id)
  }, [])

  const pulls = Object.entries(active).sort((a, b) => b[1].at - a[1].at)

  return (
    <section className="site-section mesh-feed" aria-label="Live mesh traffic">
      <h2 className="site-heading">
        <Radio size={16} aria-hidden="true" /> Mesh traffic
      </h2>
      {error && <div className="grid-status grid-error">{error}</div>}
      {pulls.length > 0 && (
        <div className="mesh-active" aria-label="Transfers in flight">
          {pulls.map(([eventId, a]) => {
            const done = (a.t.offset ?? 0) + (a.t.bytes ?? 0)
            const total = a.t.total ?? 0
            const pct = total ? Math.min(99, Math.round((done / total) * 100)) : 0
            const etaS = total && a.t.bps ? Math.max(0, (total - done) / a.t.bps) : null
            const eta =
              etaS == null
                ? ''
                : etaS < 50
                  ? ' · under a minute left'
                  : ` · ~${Math.max(1, Math.round(etaS / 60))} min left`
            return (
              <div key={eventId} className="mesh-active-row">
                <div className="mesh-active-line">
                  <span className="mesh-node mesh-kind-camera">{a.node}</span>
                  <span className="mesh-active-desc">
                    {tierLabel(a.t.quality)} pull {eventId} — chunk {a.t.chunk}/{a.t.chunks}
                    {total
                      ? ` · ${Math.round(done / 1024)} of ${Math.round(total / 1024)} KB`
                      : ''}
                    {eta}
                  </span>
                  <span className="mesh-active-pct">{pct}%</span>
                </div>
                <div
                  className="mesh-active-track"
                  role="progressbar"
                  aria-valuenow={pct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div className="mesh-active-fill" style={{ width: `${pct}%` }} />
                </div>
              </div>
            )
          })}
        </div>
      )}
      {!error && events.length === 0 && (
        <p className="mesh-empty">Waiting for mesh events…</p>
      )}
      <ol className="mesh-rows">
        {events.map((e, i) => {
          const key = `${e.at}-${i}`
          const pkt = e.extra?.packet
          const xfer = e.extra?.transfer
          const expandable = Boolean(pkt || xfer)
          const open = expanded === key
          return (
            <li key={key} className={`mesh-row${expandable ? ' mesh-has-packet' : ''}`}>
              <button
                type="button"
                className="mesh-line"
                onClick={() => expandable && setExpanded(open ? null : key)}
                disabled={!expandable}
                aria-expanded={expandable ? open : undefined}
              >
                <span className="mesh-time">{timeAgo(e.at)}</span>
                <span className={`mesh-node mesh-kind-${e.kind}`}>{e.node}</span>
                <span className="mesh-desc">{describe(e)}</span>
                {signal(e) && <span className="mesh-signal">{signal(e)}</span>}
              </button>
              {open && xfer && (
                <div className="mesh-packet">
                  <div className="mesh-packet-meta">
                    {xfer.reassembled != null ? 'photo transfer complete' : 'photo transfer'} ·{' '}
                    {xfer.event_id}
                  </div>
                  <table className="mesh-fields">
                    <tbody>
                      {transferFields(xfer).map((f) => (
                        <tr key={f.label}>
                          <td className="mesh-field-label">{f.label}</td>
                          <td className="mesh-field-hex">
                            <code>{f.value}</code>
                            {f.note && <span className="mesh-field-note"> — {f.note}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {open && !xfer && pkt && (
                <div className="mesh-packet">
                  <div className="mesh-packet-meta">
                    {pkt.len} bytes over the air · type {decodePacket(pkt.hex).type}
                  </div>
                  <table className="mesh-fields">
                    <tbody>
                      {decodePacket(pkt.hex).fields.map((f) => (
                        <tr key={f.label}>
                          <td className="mesh-field-label">{f.label}</td>
                          <td className="mesh-field-hex">
                            <code>{f.hex}</code>
                            {f.note && <span className="mesh-field-note"> — {f.note}</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}
