import { ArrowRight, Camera, Cloud, Radio, RadioTower, Router, Wifi } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { timeAgo } from '../format'
import { nodeStatus, STATUS_LABEL } from '../nodes'
import { navigate, nodeUrl } from '../router'
import type { NodeHealth } from '../types'

const KIND_ICON: Record<string, LucideIcon> = {
  camera: Camera,
  relay: RadioTower,
  gateway: Router,
}

interface UplinkPanelProps {
  node: NodeHealth
  /** Full health list — used to find the site's gateway / count its mesh peers. */
  nodes: NodeHealth[]
}

function Hop({ icon: Icon, label }: { icon: LucideIcon; label: string }) {
  return (
    <span className="uplink-hop">
      <span className="uplink-hop-label">
        <Icon size={12} aria-hidden="true" />
        {label}
      </span>
      <ArrowRight size={14} aria-hidden="true" />
    </span>
  )
}

/** Plain-language "how does this node reach the internet" panel.
 *
 * Cameras/relays: this node → 915 MHz LoRa → the site's gateway → WiFi → app.
 * Gateways: the site's mesh nodes → LoRa → this gateway → WiFi → app.
 * The gateway is the concept people trip over, so the point here is to *show*
 * the path rather than assume the reader knows what a gateway is. */
export default function UplinkPanel({ node, nodes }: UplinkPanelProps) {
  const isGateway = node.kind === 'gateway'
  const gateway = isGateway
    ? node
    : (nodes.find((n) => n.kind === 'gateway' && n.site_slug === node.site_slug) ?? null)

  const extra = gateway?.latest?.extra ?? null
  const ssid = typeof extra?.wifi_ssid === 'string' ? extra.wifi_ssid : null
  const profile = typeof extra?.lora_profile === 'string' ? extra.lora_profile : null
  const meshContactS =
    typeof extra?.last_mesh_contact_s === 'number' ? extra.last_mesh_contact_s : null
  const uploads = typeof extra?.uploads === 'number' ? extra.uploads : null

  const NodeIcon = KIND_ICON[node.kind] ?? Radio
  const meshPeers = isGateway
    ? nodes.filter((n) => n.site_slug === node.site_slug && n.kind !== 'gateway').length
    : 0

  const gatewayChip = gateway && (
    <span className="uplink-chip uplink-chip-strong">
      <span
        className={`status-dot status-${nodeStatus(gateway.last_seen_at)}`}
        aria-hidden="true"
      />
      <Router size={14} aria-hidden="true" />
      {gateway.name}
    </span>
  )

  return (
    <section className="uplink-panel" aria-label="How this node reaches the internet">
      <div className="uplink-path">
        {isGateway ? (
          <span className="uplink-chip">
            <RadioTower size={14} aria-hidden="true" />
            {meshPeers > 0
              ? `${meshPeers} mesh node${meshPeers === 1 ? '' : 's'}`
              : 'mesh nodes'}
          </span>
        ) : (
          <span className="uplink-chip uplink-chip-strong">
            <NodeIcon size={14} aria-hidden="true" />
            {node.name}
          </span>
        )}

        <Hop icon={Radio} label="LoRa 915 MHz" />

        {isGateway ? (
          gatewayChip
        ) : gateway ? (
          <button
            type="button"
            className="uplink-chip uplink-chip-strong uplink-chip-link"
            onClick={() => navigate(nodeUrl(gateway.id))}
            aria-label={`Open gateway ${gateway.name}`}
          >
            <span
              className={`status-dot status-${nodeStatus(gateway.last_seen_at)}`}
              aria-hidden="true"
            />
            <Router size={14} aria-hidden="true" />
            {gateway.name}
          </button>
        ) : (
          <span className="uplink-chip">
            <Router size={14} aria-hidden="true" />
            no gateway yet
          </span>
        )}

        <Hop icon={Wifi} label={ssid ? `WiFi ${ssid}` : 'WiFi'} />

        <span className="uplink-chip">
          <Cloud size={14} aria-hidden="true" />
          meshcam
        </span>
      </div>

      <p className="uplink-blurb">
        {isGateway ? (
          <>
            A <strong>gateway</strong> is the mesh&apos;s bridge to the internet: it is
            plugged into power, always listening on 915&nbsp;MHz LoRa for the site&apos;s
            cameras and relays, and it uploads their photos and heartbeats to this app
            over WiFi.
          </>
        ) : gateway ? (
          <>
            This {node.kind} has no internet connection of its own — it talks over
            915&nbsp;MHz LoRa radio to <strong>{gateway.name}</strong>, the site&apos;s
            gateway, which uploads its photos and heartbeats to this app over WiFi.
          </>
        ) : (
          <>
            This {node.kind} talks over 915&nbsp;MHz LoRa radio to a gateway, which
            uploads its photos to this app — but no gateway has reported from this site
            yet.
          </>
        )}
      </p>

      {gateway && (
        <div className="node-facts">
          {!isGateway && (
            <span className="node-fact">
              <span className="node-fact-label">Gateway</span>{' '}
              {STATUS_LABEL[nodeStatus(gateway.last_seen_at)]}
              {gateway.last_seen_at ? `, seen ${timeAgo(gateway.last_seen_at)}` : ''}
            </span>
          )}
          {meshContactS != null && (
            <span className="node-fact">
              <span className="node-fact-label">Last mesh contact</span> {meshContactS}s
              ago
            </span>
          )}
          {profile && (
            <span className="node-fact">
              <span className="node-fact-label">Radio profile</span> {profile}
            </span>
          )}
          {uploads != null && (
            <span className="node-fact">
              <span className="node-fact-label">Images forwarded</span> {uploads}
            </span>
          )}
        </div>
      )}
    </section>
  )
}
