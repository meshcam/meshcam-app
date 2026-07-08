# Marketing screenshots

Self-contained pipeline that boots a throwaway trailcam stack (sqlite +
[moto](https://docs.getmoto.org/) standing in for S3), seeds it with a
believable week of demo data, and captures app screenshots with Playwright
for the Montmere MeshCam marketing site (homelab repo,
`kubernetes/apps/meshcam/`). Adapted from the TPAStream webapp pipeline
(`LakeEriePartners/stream:scripts/screenshots`).

```
scripts/screenshots/
├── run.sh          One-shot: moto + build + migrate + serve + seed + capture.
├── seed.py         Hemlock Hollow (the demo property): 13 nodes, thumbnail-only
│                   photos, 7d telemetry backfill, the simulator's originals/ and
│                   device token. Written through the backend's own models so
│                   received_at can be backdated.
├── trailcamify.py  Makes the stock fixtures match the leaf hardware (QXGA
│                   sensor): detector bbox-crop thumbnails (few KB, no OSD — the
│                   detector runs on the raw framebuffer), full frames with the
│                   burned-in OSD bar at standard/max, all encoded to firmware-
│                   style byte budgets.
├── capture.mjs     Playwright runner; shots catalog lives inline.
├── fixtures/       CC-licensed wildlife JPEGs + manifest.json (attribution).
└── package.json    @playwright/test.
```

The property model + mesh beat shapes live in `backend/src/trailcam/demomesh.py`,
shared with the **live simulator** (`trailcam.meshsim`): the seed backfills
history (check-ins with announce packets, motion alerts, gateway heartbeats),
and the simulator animates the same roster live — heartbeats, command polling,
and visitor-requested full-res transfers chunked at real LoRa speed. Photos
seed as thumbnails only; quality arrives through the real pipeline when
requested, and `trailcam.demosweep` takes it back ~1 h later. Event shapes
mirror what the real gateway posts; every identity/key/SSID is generated —
nothing is copied from the live mesh, and the property is invented.

## Run

```bash
scripts/screenshots/run.sh               # everything; PNGs → /tmp/trailcam-shots/png/
scripts/screenshots/run.sh feed-phone    # fresh stack, one shot
SHOTS_HEADLESS=0 ... capture.mjs         # watch the browser (capture only)
```

Needs: `uv`, node 20+, network on first run (`npm install`, chromium
download, moto via uvx). Nothing here touches dev/prod — the stack lives
under `/tmp/trailcam-shots` on ports 8100/3901.

## Auth

`GET /auth/screenshot-login` mints the session (user `demo@montmere.com`).
It 404s unless **both** `TRAILCAM_ENV=dev` and `TRAILCAM_SCREENSHOT_LOGIN=1`
— the deployed dev overlay runs `env=dev` on a reachable URL, so the extra
flag keeps the bypass local-only.

## Fixtures & licensing

`fixtures/*.jpg` are CC0 / PDM / CC-BY wildlife photos (Openverse-sourced;
several are genuine IR trail-camera captures with the original vendor's
overlay bar cropped off). `fixtures/manifest.json` records title, creator,
license, and source URL per image — **CC-BY entries need attribution
wherever screenshots showing them are published** (the marketing site
carries a credits note).

## Shots

| id | viewport | what |
|---|---|---|
| `feed-desktop` | 1440×900 | photo feed, both sites |
| `feed-phone` | 393×852 | photo feed, phone |
| `photo-detail` | 1440×900 | first photo opened (HD-pending badge) |
| `photo-detail-phone` | 393×852 | same, phone |
| `nodes` | 1440×900 | node health cards |
| `node-detail` | 1440×900 | Food Plot telemetry charts (7d) |

Add a shot: append to `SHOTS` in `capture.mjs` — `{id, route, viewport,
setup?}`, where `setup` drives the real DOM (click a tile, wait for images
to paint; never wait on networkidle — SSE keeps the connection open).

Seed tweaks: `seed.py` is deterministic (`random.Random(2026)`); the photo
catalog and node roster are plain tables at the top.
