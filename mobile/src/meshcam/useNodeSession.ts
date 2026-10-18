/*
 * A GATT session with one leaf: connect, read status, follow notifies, send
 * commands. The whole session is a human standing at the camera with the app
 * foregrounded, so there is no reconnect logic and no background mode.
 *
 * Two firmware behaviours shape this hook (leaf_ble.cpp bench notes):
 *  - Writing "wifi" makes the leaf tear BLE down to free the radio for WiFi.
 *    The disconnect that follows is SUCCESS, not an error, so the hook
 *    remembers a wifi command is in flight and reports "handedOff".
 *  - The negotiated MTU is not guaranteed; request 247 (Android honours it,
 *    iOS negotiates its own) but never assume more than 23.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { Device, Subscription } from "react-native-ble-plx";
import {
  CMD_SLEEP,
  CMD_WIFI,
  fromBase64,
  MESHCAM_SERVICE_UUID,
  MESHCAM_STATUS_UUID,
  parseNodeStatus,
  toBase64,
  type NodeStatus,
} from "./ble";

export type SessionState = "connecting" | "connected" | "handedOff" | "disconnected" | "error";

export function useNodeSession(device: Device) {
  const [state, setState] = useState<SessionState>("connecting");
  const [status, setStatus] = useState<NodeStatus | null>(null);
  const [rssi, setRssi] = useState<number | null>(device.rssi ?? null);
  const [error, setError] = useState<string | null>(null);
  const wifiRequested = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let notifySub: Subscription | null = null;
    let disconnectSub: Subscription | null = null;
    let rssiTimer: ReturnType<typeof setInterval> | null = null;

    (async () => {
      try {
        disconnectSub = device.onDisconnected(() => {
          if (cancelled) return;
          setState(wifiRequested.current ? "handedOff" : "disconnected");
        });

        const dev = await device.connect({ requestMTU: 247 });
        await dev.discoverAllServicesAndCharacteristics();
        if (cancelled) return;
        setState("connected");

        const first = await dev.readCharacteristicForService(MESHCAM_SERVICE_UUID, MESHCAM_STATUS_UUID);
        if (first.value && !cancelled) setStatus(parseNodeStatus(fromBase64(first.value)));

        notifySub = dev.monitorCharacteristicForService(
          MESHCAM_SERVICE_UUID,
          MESHCAM_STATUS_UUID,
          (err, chr) => {
            if (err || !chr?.value || cancelled) return;
            const s = parseNodeStatus(fromBase64(chr.value));
            if (s) setStatus(s);
          },
        );

        rssiTimer = setInterval(async () => {
          try {
            const d = await dev.readRSSI();
            if (!cancelled) setRssi(d.rssi ?? null);
          } catch {
            // Link is going or gone; onDisconnected owns the state change.
          }
        }, 2_000);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setState(wifiRequested.current ? "handedOff" : "error");
      }
    })();

    return () => {
      cancelled = true;
      notifySub?.remove();
      disconnectSub?.remove();
      if (rssiTimer) clearInterval(rssiTimer);
      device.cancelConnection().catch(() => {});
    };
  }, [device]);

  const sendCommand = useCallback(
    async (cmd: typeof CMD_WIFI | typeof CMD_SLEEP) => {
      if (cmd === CMD_WIFI) wifiRequested.current = true;
      try {
        await device.writeCharacteristicWithResponseForService(
          MESHCAM_SERVICE_UUID,
          MESHCAM_STATUS_UUID,
          toBase64(cmd),
        );
      } catch (e) {
        // "sleep" and "wifi" both end in the leaf dropping the link, often
        // before the write response makes it back. Only surface the failure
        // when no teardown was asked for.
        if (cmd !== CMD_WIFI && cmd !== CMD_SLEEP) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    },
    [device],
  );

  return { state, status, rssi, error, sendCommand };
}
