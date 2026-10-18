/*
 * MeshCam mobile — the BLE walk-up shell.
 *
 * Two screens, no navigation library yet: the ranked scan list, and one
 * node's status + commands. Live view and the WiFi transfer flows come next
 * and will decide whether react-navigation earns its place.
 */
import { StatusBar } from "expo-status-bar";
import React, { useEffect, useState } from "react";
import type { Device } from "react-native-ble-plx";
import { requestBlePermissions } from "./src/meshcam/permissions";
import { NodeScreen } from "./src/screens/NodeScreen";
import { ScanScreen } from "./src/screens/ScanScreen";

export default function App() {
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState<Device | null>(null);

  useEffect(() => {
    requestBlePermissions().then((err) => {
      setPermissionError(err);
      setReady(err === null);
    });
  }, []);

  return (
    <>
      <StatusBar style="light" />
      {selected ? (
        <NodeScreen device={selected} onBack={() => setSelected(null)} />
      ) : (
        <ScanScreen ready={ready && !selected} permissionError={permissionError} onSelect={setSelected} />
      )}
    </>
  );
}
