import { Antenna, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import { UnauthorizedError } from '../api'
import { idMatchesRef } from '../ids'
import { live } from '../live'
import { statusRank } from '../nodes'
import { closeOverlay, navigate, nodesUrl, nodeUrl } from '../router'
import type { NodeHealth, Site } from '../types'
import MeshFeed from './MeshFeed'
import NodeCard from './NodeCard'
import NodeDetail from './NodeDetail'

interface NodesViewProps {
  sites: Site[]
  /** Deep-linked node id (or prefix ref) from the URL (`/nodes/<id>`), or null. */
  nodeId: string | null
  /** Public read-only demo: hides the node command panel. */
  demo: boolean
  onUnauthorized: () => void
}

interface SiteGroup {
  slug: string
  name: string
  nodes: NodeHealth[]
}

export default function NodesView({ sites, nodeId, demo, onUnauthorized }: NodesViewProps) {
  const [nodes, setNodes] = useState<NodeHealth[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getNodesHealth()
      .then((data) => {
        if (!cancelled) setNodes(data)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (err instanceof UnauthorizedError) {
          onUnauthorized()
          return
        }
        setError(err instanceof Error ? err.message : 'Failed to load nodes')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [onUnauthorized])

  // Fetches on mount — the view is remounted on each tab switch, so the list
  // is fresh every time the user lands here.
  useEffect(() => load(), [load])

  // Live: a telemetry heartbeat landed — refresh the health list (debounced;
  // beacons arrive in bursts when the gateway drains a spool).
  useEffect(() => {
    let timer: number | undefined
    const off = live.subscribe('node', () => {
      if (timer !== undefined) window.clearTimeout(timer)
      timer = window.setTimeout(() => load(), 2000)
    })
    return () => {
      off()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [load])

  const groups = useMemo<SiteGroup[]>(() => {
    const bySite = new Map<string, NodeHealth[]>()
    for (const node of nodes) {
      const list = bySite.get(node.site_slug)
      if (list) list.push(node)
      else bySite.set(node.site_slug, [node])
    }
    for (const list of bySite.values()) {
      list.sort((a, b) => statusRank(a) - statusRank(b) || a.name.localeCompare(b.name))
    }
    const ordered: SiteGroup[] = []
    for (const site of sites) {
      const list = bySite.get(site.slug)
      if (list) {
        ordered.push({ slug: site.slug, name: site.name, nodes: list })
        bySite.delete(site.slug)
      }
    }
    for (const slug of [...bySite.keys()].sort()) {
      const list = bySite.get(slug)
      if (list) ordered.push({ slug, name: slug, nodes: list })
    }
    return ordered
  }, [nodes, sites])

  // The URL id may be a generous prefix ref (ids.ts), so match accordingly.
  const selected =
    nodeId == null ? null : (nodes.find((n) => idMatchesRef(n.id, nodeId)) ?? null)

  // Deep link housekeeping once the list has loaded cleanly: canonicalize a
  // prefix ref to the full id; replace-navigate away from an unknown node.
  useEffect(() => {
    if (nodeId == null || loading || error !== null) return
    const match = nodes.find((n) => idMatchesRef(n.id, nodeId))
    if (match === undefined) {
      navigate(nodesUrl(), { replace: true })
    } else if (match.id !== nodeId) {
      navigate(nodeUrl(match.id), { replace: true })
    }
  }, [nodeId, loading, error, nodes])

  const isEmpty = !loading && !error && nodes.length === 0

  return (
    <main className="grid-wrap nodes-wrap">
      <details className="view-about">
        <summary>What are nodes?</summary>
        <p>
          Every LoRa device in the mesh: <b>cameras</b> take the photos,{' '}
          <b>relays</b> re-broadcast to stretch coverage, and the <b>gateway</b>{' '}
          bridges the mesh to this server. Nothing is registered by hand — a
          node exists here because it was heard from (first photo or telemetry
          heartbeat), and battery, temperature and signal ride along on every
          check-in.
        </p>
        <p>
          Status is time-since-last-heard: <b>OK</b> under 24&nbsp;h,{' '}
          <b>Quiet</b> 24–48&nbsp;h, <b>Dark</b> past 48&nbsp;h — problems sort
          to the top of each site. Battery colours use LiFePO4 thresholds
          (≥3.25&nbsp;V healthy, below 3.0&nbsp;V critical). Open a card for
          telemetry history and operator commands (maintenance window, sleep,
          firmware update); commands are <b>pull-based</b> — nodes sleep
          between check-ins, so queued work takes effect at the next contact,
          not instantly. The mesh feed below is the raw radio traffic as the
          gateway hears it, newest first.
        </p>
      </details>

      <div className="nodes-toolbar">
        <button
          type="button"
          className="btn"
          onClick={load}
          disabled={loading}
          aria-label="Refresh node health"
        >
          <RefreshCw size={16} aria-hidden="true" />
          Refresh
        </button>
      </div>

      {groups.map((group) => (
        <section key={group.slug} className="site-section" aria-label={group.name}>
          <h2 className="site-heading">{group.name}</h2>
          <div className="node-grid">
            {group.nodes.map((node) => (
              <NodeCard
                key={node.id}
                node={node}
                onClick={() => navigate(nodeUrl(node.id))}
              />
            ))}
          </div>
        </section>
      ))}

      <MeshFeed onUnauthorized={onUnauthorized} />

      {loading && nodes.length === 0 && (
        <div className="grid-status" role="status">
          <span className="spinner" />
          <span>Loading nodes…</span>
        </div>
      )}

      {error && !loading && (
        <div className="grid-status grid-error" role="alert">
          <span>{error}</span>
          <button type="button" className="btn" onClick={load}>
            Retry
          </button>
        </div>
      )}

      {isEmpty && (
        <div className="empty-state">
          <div className="empty-emblem" aria-hidden="true">
            <Antenna size={48} />
          </div>
          <p className="empty-title">No nodes yet</p>
          <p className="empty-hint">
            Nodes appear here on their first photo ingest or telemetry heartbeat.
          </p>
        </div>
      )}

      {selected && (
        <NodeDetail
          node={selected}
          nodes={nodes}
          demo={demo}
          onClose={() => closeOverlay(nodesUrl())}
          onUnauthorized={onUnauthorized}
          onRenamed={(id, name) =>
            setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, name } : n)))
          }
        />
      )}
    </main>
  )
}
