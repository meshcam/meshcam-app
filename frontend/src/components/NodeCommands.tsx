import { Cpu, HardDrive, Moon, RefreshCw, Wrench } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import * as api from '../api'
import { UnauthorizedError } from '../api'
import { timeAgo } from '../format'
import type { NodeCommand, NodeCommandKind, NodeHealth } from '../types'

interface NodeCommandsProps {
  node: NodeHealth
  onUnauthorized: () => void
}

const KIND_LABELS: Record<string, string> = {
  fetch_full: 'Photo pull (HD / original)',
  maintenance: 'Maintenance mode',
  sleep: 'Sleep',
  update_firmware: 'Firmware update',
  format_card: 'Erase and format the card',
}

/**
 * Operator downlink panel: queue maintenance/sleep/firmware commands and show
 * the node's recent command history. Commands are pulled by the gateway and
 * delivered on the node's next announce — queueing is always "accepted", the
 * history's status column is where progress shows up.
 */
export default function NodeCommands({ node, onUnauthorized }: NodeCommandsProps) {
  const [commands, setCommands] = useState<NodeCommand[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState<'maintenance' | 'update_firmware' | null>(null)
  const [confirmingSleep, setConfirmingSleep] = useState(false)
  const [confirmingFormat, setConfirmingFormat] = useState(false)
  const [busy, setBusy] = useState(false)

  // Maintenance form
  const [minutes, setMinutes] = useState('15')
  const [ssid, setSsid] = useState('')
  const [psk, setPsk] = useState('')
  // Firmware form
  const [fwUrl, setFwUrl] = useState('')
  const [fwSha, setFwSha] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api
      .getNodeCommands(node.id)
      .then((data) => {
        setCommands(data)
        setError(null)
      })
      .catch((err: unknown) => {
        if (err instanceof UnauthorizedError) onUnauthorized()
        else setError('Failed to load command history')
      })
      .finally(() => setLoading(false))
  }, [node.id, onUnauthorized])

  useEffect(() => {
    setForm(null)
    setConfirmingSleep(false)
    setConfirmingFormat(false)
    load()
  }, [load])

  const queue = useCallback(
    async (kind: NodeCommandKind, payload?: Record<string, unknown>) => {
      setBusy(true)
      setError(null)
      try {
        await api.queueNodeCommand(node.id, kind, payload)
        setForm(null)
        setConfirmingSleep(false)
        setConfirmingFormat(false)
        load()
      } catch (err: unknown) {
        if (err instanceof UnauthorizedError) onUnauthorized()
        else setError('Failed to queue command')
      } finally {
        setBusy(false)
      }
    },
    [node.id, load, onUnauthorized],
  )

  const submitMaintenance = () => {
    const payload: Record<string, unknown> = {}
    const mins = Number(minutes)
    if (Number.isFinite(mins) && mins > 0) payload.minutes = mins
    if (ssid.trim()) payload.ssid = ssid.trim()
    if (psk) payload.psk = psk
    void queue('maintenance', payload)
  }

  const submitFirmware = () => {
    const payload: Record<string, unknown> = {
      url: fwUrl.trim(),
      sha256: fwSha.trim(),
    }
    if (ssid.trim()) payload.ssid = ssid.trim()
    if (psk) payload.psk = psk
    void queue('update_firmware', payload)
  }

  return (
    <div className="node-commands">
      <div className="node-commands-head">
        <h3 className="node-commands-title">Commands</h3>
        <button
          type="button"
          className="icon-btn"
          aria-label="Refresh command history"
          onClick={load}
        >
          <RefreshCw size={14} aria-hidden="true" />
        </button>
      </div>

      <div className="node-commands-actions">
        <button
          type="button"
          className={`btn${form === 'maintenance' ? ' active' : ''}`}
          disabled={busy}
          onClick={() => {
            setForm(form === 'maintenance' ? null : 'maintenance')
            setConfirmingSleep(false)
            setConfirmingFormat(false)
          }}
        >
          <Wrench size={15} aria-hidden="true" />
          Maintenance…
        </button>
        {confirmingSleep ? (
          <span className="confirm-group">
            <span className="confirm-text">End the maintenance window now?</span>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void queue('sleep')}
            >
              <Moon size={15} aria-hidden="true" />
              Send sleep
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => setConfirmingSleep(false)}
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => {
              setConfirmingSleep(true)
              setForm(null)
              setConfirmingFormat(false)
            }}
          >
            <Moon size={15} aria-hidden="true" />
            Sleep
          </button>
        )}
        <button
          type="button"
          className={`btn${form === 'update_firmware' ? ' active' : ''}`}
          disabled={busy}
          onClick={() => {
            setForm(form === 'update_firmware' ? null : 'update_firmware')
            setConfirmingSleep(false)
            setConfirmingFormat(false)
          }}
        >
          <Cpu size={15} aria-hidden="true" />
          Firmware…
        </button>
        {/*
          Only offered when the node has actually reported a card it cannot read
         . Deliberately not a always-available button: this is the one
          command that destroys data, and there is no reason to offer it against a
          camera whose card is fine. The node refuses anyway, but the UI should not
          be inviting people to try.
        */}
        {node.storage_state === 'unreadable' &&
          (confirmingFormat ? (
            <span className="confirm-group">
              <span className="confirm-text">
                Erase everything on this card and format it FAT32? Anything still on it
                is gone. If it might hold photos, read it on a computer first.
              </span>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => void queue('format_card', { confirm: 'erase' })}
              >
                <HardDrive size={15} aria-hidden="true" />
                Erase and format
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => setConfirmingFormat(false)}
              >
                Cancel
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="btn"
              disabled={busy}
              title="This node reports a card it cannot read. Cards over 32 GB ship formatted exFAT, which the camera can't mount; formatting it FAT32 fixes that and erases the card."
              onClick={() => {
                setConfirmingFormat(true)
                setForm(null)
                setConfirmingSleep(false)
              }}
            >
              <HardDrive size={15} aria-hidden="true" />
              Format card…
            </button>
          ))}
      </div>

      {form === 'maintenance' && (
        <form
          className="node-command-form"
          onSubmit={(e) => {
            e.preventDefault()
            submitMaintenance()
          }}
        >
          <p className="settings-hint">
            Keeps the node awake with its command channels open, delivered on its next
            announce. WiFi credentials are optional; without them it just stays awake.
          </p>
          <div className="node-form-row">
            <label className="node-form-field">
              Minutes
              <input
                className="settings-input"
                type="number"
                min={1}
                max={240}
                value={minutes}
                onChange={(e) => setMinutes(e.target.value)}
              />
            </label>
            <label className="node-form-field">
              WiFi SSID (optional)
              <input
                className="settings-input"
                value={ssid}
                onChange={(e) => setSsid(e.target.value)}
              />
            </label>
            <label className="node-form-field">
              WiFi password (optional)
              <input
                className="settings-input"
                type="password"
                value={psk}
                onChange={(e) => setPsk(e.target.value)}
              />
            </label>
          </div>
          <div className="notes-actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Queue maintenance
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => setForm(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {form === 'update_firmware' && (
        <form
          className="node-command-form"
          onSubmit={(e) => {
            e.preventDefault()
            submitFirmware()
          }}
        >
          <p className="settings-hint">
            The node streams the image from the URL into its idle OTA slot and verifies the
            sha256 before committing. Needs WiFi credentials the node can reach.
          </p>
          <div className="node-form-row">
            <label className="node-form-field wide">
              Image URL
              <input
                className="settings-input"
                required
                placeholder="https://…/firmware.bin"
                value={fwUrl}
                onChange={(e) => setFwUrl(e.target.value)}
              />
            </label>
            <label className="node-form-field wide">
              SHA-256
              <input
                className="settings-input"
                required
                placeholder="hex digest"
                value={fwSha}
                onChange={(e) => setFwSha(e.target.value)}
              />
            </label>
            <label className="node-form-field">
              WiFi SSID
              <input
                className="settings-input"
                value={ssid}
                onChange={(e) => setSsid(e.target.value)}
              />
            </label>
            <label className="node-form-field">
              WiFi password
              <input
                className="settings-input"
                type="password"
                value={psk}
                onChange={(e) => setPsk(e.target.value)}
              />
            </label>
          </div>
          <div className="notes-actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Queue firmware update
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => setForm(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && (
        <div className="settings-error" role="alert">
          {error}
        </div>
      )}

      {loading ? (
        <div className="node-fine">
          <span className="spinner" /> Loading history…
        </div>
      ) : commands.length === 0 ? (
        <div className="node-fine">No commands yet.</div>
      ) : (
        <ul className="node-command-list">
          {commands.map((c) => (
            <li key={c.id} className="node-command-row">
              <span className="node-command-kind">{KIND_LABELS[c.kind] ?? c.kind}</span>
              <span className={`hd-diag-badge ${c.status}`}>{c.status}</span>
              <span className="node-command-when">
                {timeAgo(c.created_at)}
                {c.requested_by ? ` · ${c.requested_by.split('@')[0]}` : ''}
              </span>
              {c.detail && <span className="node-command-detail">{c.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
