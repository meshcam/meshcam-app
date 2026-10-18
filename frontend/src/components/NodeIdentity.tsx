import { Split } from 'lucide-react'
import { useEffect, useState } from 'react'
import * as api from '../api'
import { UnauthorizedError } from '../api'
import type { NodeHealth } from '../types'

interface NodeIdentityProps {
  node: NodeHealth
  /** Full health list — used to name the row holding the other half. */
  nodes: NodeHealth[]
  /** Public read-only demo: explain the split, offer no merge. */
  demo: boolean
  onUnauthorized: () => void
  /** The merge went through; `keptId` is the row that survived it. */
  onMerged: (keptId: string) => void
}

/**
 * The visible half of a re-identification.
 *
 * A leaf's wire slug comes from its RNS identity. Lose the identity and the
 * same board reappears under a new slug with a fresh camera row, so its photos
 * and telemetry are filed in two places. Since leaf-0.21.0 the board
 * says so itself and the gateway forwards it as `extra.prev_node`; until this
 * panel that fact lived only in a JSON column.
 *
 * The merge offered here keeps the OLDER row — the one the retired slug names.
 * That row carries the name someone typed, its map position, its notes and the
 * id every existing photo link points at; the new row carries a machine-made
 * name and a few hours of history. The board goes on announcing under its new
 * slug forever, which is what the alias is for.
 */
export default function NodeIdentity({
  node,
  nodes,
  demo,
  onUnauthorized,
  onMerged,
}: NodeIdentityProps) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Switching nodes inside the overlay must not carry a half-armed confirm.
  useEffect(() => {
    setConfirming(false)
    setError(null)
  }, [node.id])

  if (!node.prev_node) return null

  const target = nodes.find((n) => n.id === node.prev_node_id) ?? null

  const merge = () => {
    if (!target) return
    setBusy(true)
    setError(null)
    api
      .mergeCamera(target.id, node.id, `${node.slug} reported prev_node ${node.prev_node}`)
      .then(() => onMerged(target.id))
      .catch((err: unknown) => {
        if (err instanceof UnauthorizedError) onUnauthorized()
        else setError(err instanceof Error ? err.message : 'Merge failed')
      })
      .finally(() => {
        setBusy(false)
        setConfirming(false)
      })
  }

  return (
    <section className="node-identity" aria-label="Identity change">
      <h3 className="node-identity-title">
        <Split size={15} aria-hidden="true" />
        This node's history is split
      </h3>
      <p className="node-identity-body">
        It last checked in as <code>{node.slug}</code>, but it reports having
        announced as <code>{node.prev_node}</code> before that. A board gets a new
        name when it loses its radio identity — a full reflash, or the settings
        area filling up — and everything it sent under the old name is filed
        separately.
      </p>
      {target ? (
        <>
          <p className="node-identity-body">
            That older half is on <b>{target.name}</b> (<code>{target.slug}</code>).
            Merging moves this row's photos, telemetry and queued commands onto it
            and keeps <code>{node.slug}</code> as an alias, so the board's next
            check-in still lands there. This row then goes away.
          </p>
          {demo ? null : confirming ? (
            <span className="confirm-group">
              <span className="confirm-text">
                Merge into {target.name}, keeping that node's name, position and
                photo links?
              </span>
              <button type="button" className="btn" disabled={busy} onClick={merge}>
                <Split size={15} aria-hidden="true" />
                Merge into {target.name}
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => setConfirming(true)}
            >
              <Split size={15} aria-hidden="true" />
              Merge history…
            </button>
          )}
        </>
      ) : (
        <p className="node-identity-body">
          Nothing answers to <code>{node.prev_node}</code> here any more, so there is
          no second row left to merge — that history was either already folded into
          another node or has aged out. Nothing to do.
        </p>
      )}
      {error && (
        <p className="node-identity-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
