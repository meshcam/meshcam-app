# MeshCam mobile

The native companion app. It exists for the two things a web page cannot do:
rank identical cameras by live BLE RSSI ("which one am I standing in front
of"), and join the camera's WiFi without the user typing a key.

**Stack:** Expo SDK 57 / React Native 0.86 / TypeScript, `react-native-ble-plx`
(Apache-2.0). New Architecture is ON (`newArchEnabled` in `app.json`).

## What works today

- Scan screen: leaves advertising the MeshCam service UUID, ranked by live
  RSSI, pruned when they stop advertising.
- Node screen: connect, read + follow the status JSON (node, fw, boot, pir,
  cap, fail, vb, ap, key), live RSSI, and the two commands the firmware
  accepts: `wifi` (leaf tears down BLE, brings up its AP — the disconnect is
  expected) and `sleep`.
- Handoff card: after `wifi`, the screen shows the camera's network name and
  WPA2 key, plus a button into Android's WiFi picker.

The firmware side of this contract is `meshcam-firmware
leaf/src/leaf_ble.{h,cpp}`. UUIDs and the status shape live in
`src/meshcam/ble.ts`; change both sides together.

Joining the network is handed to the system WiFi picker rather than done
in-app; live view and transfers stay on the web app.

### The handoff carries the credentials

The leaf's AP is WPA2 and its key is minted per node into NVS, so it exists
nowhere a person can see it. The first live test on board 0013 (2026-09-03)
handed off cleanly and then stranded the operator at the phone's password
prompt. The fix is `ap` + `key` in the status JSON.

They arrive on the **first read**, not on a notify at handoff time. Writing
`wifi` tears the whole BLE stack down, so anything pushed at that moment races
the teardown — the least reliable point in the session to deliver the one
thing the operator cannot proceed without. Reading them up front leaves no
race. Treat the pair as all-or-nothing, and expect them absent on firmware
older than `leaf-0.20.0` or on a leaf handing off to an operator-supplied
network.

## Running it

BLE needs a real device and custom native code, so Expo Go does not work.
Build a dev client:

```bash
npm install
npx expo prebuild            # generates android/ + ios/ (gitignored)
npx expo run:android         # device via USB, needs Android SDK + adb
```

A standalone sideload build (no Metro server) is
`cd android && ./gradlew assembleRelease`; the APK lands in
`android/app/build/outputs/apk/release/` signed with the debug keystore,
which is fine for bench and pilot sideloads.

No Android SDK locally? `eas build --profile development --platform android`
compiles in Expo's cloud (free tier: 15 builds/month) and hands back an
installable APK. Same for iOS (`--platform ios`), which is how iOS ships from
Linux; that path needs the Apple Developer account first.

## Known upstream issue

`react-native-ble-plx` has open crash reports on connect under the New
Architecture (dotintent/react-native-ble-plx#1277, #1278). Worth exercising
connect, status read and `sleep` repeatedly on your own hardware before
relying on it. The BLE calls are kept behind `src/meshcam/ble.ts` so the
transport can be swapped for a thin native module without touching the
screens.

## Bench notes that shaped the code

- The negotiated MTU is whatever the phone asks for, not the 247 the leaf
  prefers; the bench saw 23 against BlueZ. Reads page fine; anything written
  in one shot must fit the real MTU (matters for the future credential
  handoff, not for today's 5-byte commands).
- The leaf only advertises inside its PIR-gated wake window. An empty scan
  list usually means the camera is asleep: wave at it.
- `wifi`/`sleep` writes often lose the write response because the leaf drops
  the link immediately; the app treats that as success.
- The credentials are shown as selectable text rather than behind a copy
  button. `expo-clipboard` is a native module and would cost a rebuild, and a
  long-press already copies; the key's alphabet drops `0 O 1 I L` so it can be
  read aloud or transcribed (`leaf_portal.cpp`).
