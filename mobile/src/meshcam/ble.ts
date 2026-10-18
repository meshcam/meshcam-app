/*
 * The BLE contract with the leaf firmware. Mirror of meshcam-firmware
 * leaf/src/leaf_ble.{h,cpp} — change that file and this one together.
 *
 * The leaf advertises the 128-bit service UUID in the advertising PDU and its
 * name ("MeshCam-XXXXXX") in the scan response. One characteristic carries
 * everything: read/notify serves a small status JSON, and writing the ASCII
 * command "wifi" or "sleep" drives the node. The server prefers MTU 247, but
 * the achieved MTU is negotiated at connect time and must not be assumed
 * (bench 2026-08-17 saw 23 against BlueZ).
 */
import { BleManager } from "react-native-ble-plx";

export const MESHCAM_SERVICE_UUID = "6d657368-6361-6d00-8000-00805f9b34fb";
export const MESHCAM_STATUS_UUID = "6d657368-6361-6d01-8000-00805f9b34fb";

/** Commands the status characteristic accepts as a write. */
export const CMD_WIFI = "wifi";
export const CMD_SLEEP = "sleep";

/** Status JSON the leaf serves — same counters the RNS announce tail carries. */
export interface NodeStatus {
  node: string;
  fw: string;
  boot: number;
  pir: number;
  cap: number;
  fail: number;
  /** Battery volts; absent when the leaf could not read VBAT. */
  vb?: number;
  /**
   * The network the leaf will raise if we write "wifi", and its WPA2 key. Present
   * from the first read, not pushed at handoff time: the handoff tears the BLE
   * stack down, so anything delivered then races the teardown.
   *
   * Both are absent on a leaf whose "wifi" command joins an operator-supplied
   * network instead of raising its own, and on firmware older than leaf-0.20.0.
   * Treat the pair as all-or-nothing — the key without the name is not joinable.
   */
  ap?: string;
  key?: string;
}

/** The leaf's own address once its AP is up. Fixed by the SoftAP, not discovered. */
export const PORTAL_URL = "http://192.168.4.1/";

export function parseNodeStatus(json: string): NodeStatus | null {
  try {
    const o = JSON.parse(json);
    if (typeof o !== "object" || o === null || typeof o.node !== "string") return null;
    return o as NodeStatus;
  } catch {
    return null;
  }
}

/*
 * ble-plx speaks base64 for characteristic values. Hermes has no atob/btoa,
 * and the payloads are tiny ASCII/JSON, so hand-roll it instead of pulling in
 * a Buffer polyfill.
 */
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export function toBase64(s: string): string {
  const bytes: number[] = [];
  for (let i = 0; i < s.length; i++) {
    const c = s.codePointAt(i)!;
    if (c < 0x80) bytes.push(c);
    else if (c < 0x800) bytes.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
    else if (c < 0x10000) bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    else {
      bytes.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 0x3f), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
      i++; // consumed a surrogate pair
    }
  }
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const [a, b, c] = [bytes[i], bytes[i + 1], bytes[i + 2]];
    out += B64[a >> 2];
    out += B64[((a & 3) << 4) | ((b ?? 0) >> 4)];
    out += b === undefined ? "=" : B64[((b & 15) << 2) | ((c ?? 0) >> 6)];
    out += c === undefined ? "=" : B64[c & 63];
  }
  return out;
}

export function fromBase64(b64: string): string {
  const clean = b64.replace(/[^A-Za-z0-9+/]/g, "");
  const bytes: number[] = [];
  for (let i = 0; i < clean.length; i += 4) {
    const n = [0, 1, 2, 3].map((k) => B64.indexOf(clean[i + k] ?? "A"));
    bytes.push((n[0] << 2) | (n[1] >> 4));
    if (i + 2 < clean.length) bytes.push(((n[1] & 15) << 4) | (n[2] >> 2));
    if (i + 3 < clean.length) bytes.push(((n[2] & 3) << 6) | n[3]);
  }
  // Status payloads are ASCII JSON; decode as UTF-8 without TextDecoder.
  let out = "";
  for (let i = 0; i < bytes.length; ) {
    const a = bytes[i];
    if (a < 0x80) {
      out += String.fromCharCode(a);
      i += 1;
    } else if (a < 0xe0) {
      out += String.fromCharCode(((a & 0x1f) << 6) | (bytes[i + 1] & 0x3f));
      i += 2;
    } else if (a < 0xf0) {
      out += String.fromCharCode(((a & 0x0f) << 12) | ((bytes[i + 1] & 0x3f) << 6) | (bytes[i + 2] & 0x3f));
      i += 3;
    } else {
      const cp =
        ((a & 0x07) << 18) | ((bytes[i + 1] & 0x3f) << 12) | ((bytes[i + 2] & 0x3f) << 6) | (bytes[i + 3] & 0x3f);
      out += String.fromCodePoint(cp);
      i += 4;
    }
  }
  return out;
}

/*
 * One manager for the app's lifetime. ble-plx wants exactly one BleManager;
 * constructing per-render leaks native listeners.
 */
let manager: BleManager | null = null;

export function bleManager(): BleManager {
  if (!manager) manager = new BleManager();
  return manager;
}
