/*
 * The walk-up triage list: every advertising leaf in earshot, ranked by live
 * RSSI, so "which of these twenty identical boxes am I standing in front of"
 * answers itself as the operator walks. Today a row carries name + RSSI; when
 * the firmware starts packing battery / last-capture / queue depth into the
 * advertisement, those fields land on this row without connecting.
 */
import React from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import type { Device } from "react-native-ble-plx";
import { useNodeScan, type SeenNode } from "../meshcam/useNodeScan";

/** Rough proximity words for a walk-up user; dBm means nothing in the field. */
function proximity(rssi: number): string {
  if (rssi > -55) return "right here";
  if (rssi > -70) return "close";
  if (rssi > -85) return "nearby";
  return "far";
}

function NodeRow({ node, onPress }: { node: SeenNode; onPress: () => void }) {
  return (
    <Pressable style={styles.row} onPress={onPress}>
      <View style={styles.rowText}>
        <Text style={styles.name}>{node.name}</Text>
        <Text style={styles.sub}>
          {proximity(node.rssi)} · {node.rssi} dBm
        </Text>
      </View>
      <Text style={styles.chev}>›</Text>
    </Pressable>
  );
}

export function ScanScreen({
  ready,
  permissionError,
  onSelect,
}: {
  ready: boolean;
  permissionError: string | null;
  onSelect: (d: Device) => void;
}) {
  const { nodes, error } = useNodeScan(ready);

  return (
    <View style={styles.container}>
      <Text style={styles.title}>MeshCam</Text>
      <Text style={styles.hint}>
        Walk up to a camera and wave at it. It wakes, and shows up here.
      </Text>
      {permissionError ? <Text style={styles.error}>{permissionError}</Text> : null}
      {error ? <Text style={styles.error}>Scan failed: {error}</Text> : null}
      <FlatList
        data={nodes}
        keyExtractor={(n) => n.device.id}
        renderItem={({ item }) => <NodeRow node={item} onPress={() => onSelect(item.device)} />}
        ListEmptyComponent={
          ready && !error && !permissionError ? (
            <Text style={styles.empty}>Listening for cameras…</Text>
          ) : null
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, paddingTop: 72, paddingHorizontal: 20, backgroundColor: "#101613" },
  title: { fontSize: 30, fontWeight: "700", color: "#e8f0ea" },
  hint: { color: "#8aa392", marginTop: 6, marginBottom: 18 },
  error: { color: "#ff8a80", marginBottom: 12 },
  empty: { color: "#5d6f63", marginTop: 40, textAlign: "center" },
  row: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#1a231e",
    borderRadius: 12,
    padding: 16,
    marginBottom: 10,
  },
  rowText: { flex: 1 },
  name: { color: "#e8f0ea", fontSize: 17, fontWeight: "600" },
  sub: { color: "#8aa392", marginTop: 2 },
  chev: { color: "#8aa392", fontSize: 24 },
});
