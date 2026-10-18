/*
 * BLE runtime permissions. iOS handles its own prompt off
 * NSBluetoothAlwaysUsageDescription. Android needs a runtime request:
 * BLUETOOTH_SCAN + BLUETOOTH_CONNECT on 12+ (declared neverForLocation via
 * the ble-plx config plugin, so no Location toggle is involved), and
 * ACCESS_FINE_LOCATION on 6–11 because pre-12 Android ties BLE scanning to
 * location.
 */
import { PermissionsAndroid, Platform } from "react-native";

export async function requestBlePermissions(): Promise<string | null> {
  if (Platform.OS !== "android") return null;
  if (Platform.Version >= 31) {
    const res = await PermissionsAndroid.requestMultiple([
      PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN,
      PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT,
    ]);
    const denied = Object.entries(res).filter(([, v]) => v !== PermissionsAndroid.RESULTS.GRANTED);
    return denied.length ? "Bluetooth permission denied — enable Nearby devices for MeshCam in Settings." : null;
  }
  const res = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION);
  return res === PermissionsAndroid.RESULTS.GRANTED
    ? null
    : "Location permission denied — Android needs it for Bluetooth scanning on this OS version.";
}
