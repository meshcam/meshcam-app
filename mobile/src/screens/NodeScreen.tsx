/*
 * One connected leaf: live status (the same counters the RNS announce tail
 * carries) and the two commands the firmware understands today. "Bring up
 * WiFi" intentionally ends the BLE link — the S3 has one radio — so the
 * disconnect that follows renders as the next step, not a failure.
 *
 * That next step is the whole point of this screen: the leaf's AP is WPA2 with
 * a per-node key that exists nowhere a person can see it, so the handoff has to
 * end on a card showing the network name and key, or the operator is left at
 * their phone's password prompt with a camera they cannot reach (found
 * on board 0013). The credentials ride in the status JSON from the first
 * read, so they are already in hand when the link drops.
 */
import React from "react";
import { Linking, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import type { Device } from "react-native-ble-plx";
import { CMD_SLEEP, CMD_WIFI, PORTAL_URL, type NodeStatus } from "../meshcam/ble";
import { useNodeSession } from "../meshcam/useNodeSession";

function Field({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.fieldValue}>{value}</Text>
    </View>
  );
}

/*
 * Android can jump straight to the WiFi picker; iOS has no equivalent URL and
 * App-Prefs: is a rejection at review, so there the card just says where to go.
 * A one-tap join (WifiNetworkSuggestion / NEHotspotConfiguration) is the next
 * step up from this and needs native code, which this screen deliberately does
 * not yet require.
 */
function openWifiSettings() {
  if (Platform.OS === "android") {
    Linking.sendIntent("android.settings.WIFI_SETTINGS").catch(() => {});
  } else {
    Linking.openSettings().catch(() => {});
  }
}

/*
 * Credentials are shown as selectable monospace so they can be long-pressed and
 * copied. No clipboard button: that is a native module, and a value you cannot
 * read accurately is the actual failure being fixed here — the key alphabet
 * already drops 0/O/1/I/L for the same reason (leaf_portal.cpp).
 */
function JoinCard({ status }: { status: NodeStatus }) {
  return (
    <View style={styles.joinCard}>
      <Text style={styles.joinTitle}>Join the camera's WiFi</Text>
      <Text style={styles.joinLabel}>Network</Text>
      <Text style={styles.cred} selectable>
        {status.ap}
      </Text>
      <Text style={styles.joinLabel}>Password</Text>
      <Text style={styles.cred} selectable>
        {status.key}
      </Text>
      <Text style={styles.joinHint}>
        Join it in WiFi settings, then the camera's page opens by itself. If it does not,
        go to {PORTAL_URL} in a browser.
      </Text>
      <Pressable style={[styles.btn, styles.btnPrimary]} onPress={openWifiSettings}>
        <Text style={styles.btnText}>Open WiFi settings</Text>
      </Pressable>
    </View>
  );
}

export function NodeScreen({ device, onBack }: { device: Device; onBack: () => void }) {
  const { state, status, rssi, error, sendCommand } = useNodeSession(device);

  // Older firmware serves no ap/key, and a leaf handing off to an operator-supplied
  // network has none to give. Say what happened rather than render an empty card.
  const haveCreds = Boolean(status?.ap && status?.key);

  const stateLine = {
    connecting: "Connecting…",
    connected: "Connected",
    handedOff: haveCreds
      ? "Camera switched to WiFi — join it below"
      : "Camera switched to WiFi — it did not hand over a network name or key",
    disconnected: "Camera dropped the connection",
    error: `Connection failed${error ? `: ${error}` : ""}`,
  }[state];

  return (
    <View style={styles.container}>
      <Pressable onPress={onBack}>
        <Text style={styles.back}>‹ Cameras</Text>
      </Pressable>
      <Text style={styles.title}>{device.name ?? device.id}</Text>
      <Text style={styles.state}>{stateLine}</Text>

      {state === "handedOff" && haveCreds && status ? <JoinCard status={status} /> : null}

      {status ? (
        <View style={styles.card}>
          <Field label="Node" value={status.node} />
          <Field label="Firmware" value={status.fw} />
          <Field label="Battery" value={status.vb !== undefined ? `${status.vb.toFixed(2)} V` : "—"} />
          <Field label="Wakes (boot / PIR)" value={`${status.boot} / ${status.pir}`} />
          <Field label="Captures" value={String(status.cap)} />
          <Field label="Failed pushes" value={String(status.fail)} />
          <Field label="Signal" value={rssi !== null ? `${rssi} dBm` : "—"} />
          {haveCreds ? <Field label="Its WiFi" value={status.ap!} /> : null}
        </View>
      ) : null}

      {state === "connected" ? (
        <View style={styles.buttons}>
          <Pressable style={[styles.btn, styles.btnPrimary]} onPress={() => sendCommand(CMD_WIFI)}>
            <Text style={styles.btnText}>Bring up WiFi</Text>
          </Pressable>
          <Pressable style={styles.btn} onPress={() => sendCommand(CMD_SLEEP)}>
            <Text style={styles.btnText}>Send to sleep</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

const mono = Platform.select({ ios: "Menlo", default: "monospace" });

const styles = StyleSheet.create({
  container: { flex: 1, paddingTop: 72, paddingHorizontal: 20, backgroundColor: "#101613" },
  back: { color: "#8aa392", fontSize: 17, marginBottom: 16 },
  title: { fontSize: 26, fontWeight: "700", color: "#e8f0ea" },
  state: { color: "#8aa392", marginTop: 6, marginBottom: 18 },
  card: { backgroundColor: "#1a231e", borderRadius: 12, padding: 16, marginBottom: 24 },
  field: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 6 },
  fieldLabel: { color: "#8aa392" },
  fieldValue: { color: "#e8f0ea", fontWeight: "600" },
  joinCard: {
    backgroundColor: "#1a231e",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#2e5540",
    padding: 16,
    marginBottom: 24,
  },
  joinTitle: { color: "#e8f0ea", fontSize: 18, fontWeight: "700", marginBottom: 12 },
  joinLabel: { color: "#8aa392", fontSize: 13, marginTop: 8 },
  // Big enough to read at arm's length in the wet, which is where this gets used.
  cred: { color: "#e8f0ea", fontFamily: mono, fontSize: 22, letterSpacing: 1, marginTop: 2 },
  joinHint: { color: "#8aa392", fontSize: 13, marginTop: 14, marginBottom: 14, lineHeight: 18 },
  buttons: { gap: 10 },
  btn: { backgroundColor: "#1a231e", borderRadius: 12, padding: 16, alignItems: "center" },
  btnPrimary: { backgroundColor: "#2e5540" },
  btnText: { color: "#e8f0ea", fontSize: 16, fontWeight: "600" },
});
