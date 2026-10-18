/*
 * Live RSSI-ranked scan of nearby MeshCam leaves.
 *
 * This is the feature that forced a native app in the first place: rank
 * identical cameras by proximity, live, as the operator walks between them.
 * So the scan runs with allowDuplicates and every advertisement updates that
 * node's RSSI.
 *
 * A leaf only advertises inside its PIR-gated walk-up window and vanishes
 * when it sleeps or hands off to WiFi, so entries not heard from recently are
 * pruned rather than left looking reachable.
 */
import { useEffect, useRef, useState } from "react";
import type { Device } from "react-native-ble-plx";
import { bleManager, MESHCAM_SERVICE_UUID } from "./ble";

export interface SeenNode {
  device: Device;
  name: string;
  rssi: number;
  lastSeen: number; // ms epoch
}

const PRUNE_AFTER_MS = 10_000;
const REFRESH_MS = 1_000;

export function useNodeScan(enabled: boolean) {
  const [nodes, setNodes] = useState<SeenNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef(new Map<string, SeenNode>());

  useEffect(() => {
    if (!enabled) return;
    const mgr = bleManager();
    seen.current.clear();
    setNodes([]);
    setError(null);

    mgr.startDeviceScan([MESHCAM_SERVICE_UUID], { allowDuplicates: true }, (err, device) => {
      if (err) {
        setError(err.message);
        return;
      }
      if (!device) return;
      seen.current.set(device.id, {
        device,
        // The name rides the scan response; a plain advertisement can arrive
        // without it, so keep a previously seen name over null.
        name: device.name ?? device.localName ?? seen.current.get(device.id)?.name ?? device.id,
        rssi: device.rssi ?? seen.current.get(device.id)?.rssi ?? -127,
        lastSeen: Date.now(),
      });
    });

    // Publish on a timer instead of per-advertisement: allowDuplicates can
    // deliver many callbacks a second per node and re-rendering each one
    // makes the list jitter unreadably.
    const timer = setInterval(() => {
      const now = Date.now();
      for (const [id, n] of seen.current) {
        if (now - n.lastSeen > PRUNE_AFTER_MS) seen.current.delete(id);
      }
      setNodes([...seen.current.values()].sort((a, b) => b.rssi - a.rssi));
    }, REFRESH_MS);

    return () => {
      clearInterval(timer);
      mgr.stopDeviceScan();
    };
  }, [enabled]);

  return { nodes, error };
}
