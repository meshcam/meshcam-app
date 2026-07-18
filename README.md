# MeshCam app

The self-hostable gallery + ingest server for
[MeshCam](https://getmeshcam.com) LoRa mesh trail camera networks. FastAPI +
React. Run the whole backend yourself; the hosted cloud is optional.

**Live demo with real captures: [demo.getmeshcam.com](https://demo.getmeshcam.com)**

## What it does

- **Thumbnail-first gallery:** a ~5 KB thumbnail arrives over the mesh in
  seconds; tap ★ to request the full-resolution image, which the mesh
  delivers opportunistically (the UI shows live chunk-by-chunk transfer
  progress from telemetry).
- **Sightings, not frames:** burst photos group into one "animal visit"
  tile (per-camera capture gaps under `TRAILCAM_SIGHTING_GAP_MIN`, default
  30 min) — a real field feed collapsed 19x. Tap to page through the burst;
  `?flat=1` shows every frame. A brushable activity timeline above the grid
  jumps the feed to wherever the activity was.
- **Ingest API:** idempotent, store-and-forward-friendly; devices are
  first-class via the open spec
  ([meshcam-api](https://github.com/meshcam/meshcam-api)). Sites and
  cameras auto-create on first contact; the mesh adds itself.
- **Node health:** per-node battery (LiFePO4-aware coloring), RSSI/SNR,
  temperature charts; OK/Quiet/Dark status from last-seen.
- **Command queue:** pull-based (gateways live behind NAT; nodes sleep):
  full-res fetches, maintenance windows, sleep.
- **OSD stamping:** burns camera/temp/battery/timestamp into full-res
  images at ingest, keeping pristine originals as `.raw` S3 siblings.
- **Retention:** unsaved photos expire (default 180 days); starred never.
- **Survey map (`/survey`):** walk the property pressing the surveyor's
  button; probes plot coloured by gateway RSSI, cluster into sessions, and a
  before/after compare mode shows whether an antenna move actually helped —
  refusing to state a median from fewer than 5 matched positions. Probes are
  permanent (retention never touches them). Basemap tiles are **off by
  default**: probe coordinates are your camera locations, and third-party
  tiles would tell the tile host where they are. Opt in per browser in
  Settings, or set `TRAILCAM_MAP_TILE_URL` deployment-wide.

## Quickstart

```sh
docker compose up --build
# → http://localhost:8000  (eval login enabled; see docker-compose.yml)
```

Storage is any S3 API (MinIO in the compose file; Garage/AWS/B2 all work).
Database is SQLite out of the box, Postgres via `TRAILCAM_DATABASE_URL` or
`TRAILCAM_DB_*` parts. Human auth is OIDC: point `TRAILCAM_OIDC_*` at your
IdP (Authentik, Authelia, Keycloak…); device auth is per-device bearer
tokens:

```sh
python -m trailcam.devicetoken my-gateway   # prints once; sha256 stored
```

All configuration is `TRAILCAM_*` environment variables; see
`backend/src/trailcam/config.py` for the complete, commented list. (Env
prefix and module name keep the project's original working title,
`trailcam`; renames are churn and the API is the contract.)

## Development

```sh
cd backend && uv sync && uv run pytest         # 69 tests
uv run uvicorn trailcam.main:app --reload
cd frontend && npm ci && npm run dev
```

## License

AGPL-3.0, see [LICENSE](LICENSE). Self-hosting is free forever.
If you modify it and offer it as a network service, share your changes. For
commercial licensing outside AGPL terms: hello@getmeshcam.com.
