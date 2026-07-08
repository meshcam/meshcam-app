#!/usr/bin/env bash
# One-shot marketing-screenshot pipeline: throwaway sqlite + moto-S3 stack,
# demo seed, Playwright capture. Everything ephemeral lives under
# /tmp/trailcam-shots; nothing touches dev/prod data.
#
#   scripts/screenshots/run.sh              # full pipeline
#   scripts/screenshots/run.sh feed-phone   # re-capture one shot (fresh stack)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK=/tmp/trailcam-shots
PORT=8100
MOTO_PORT=3901

rm -rf "$WORK"
mkdir -p "$WORK"

export TRAILCAM_ENV=dev
export TRAILCAM_SCREENSHOT_LOGIN=1
export TRAILCAM_PUBLIC_URL="http://localhost:$PORT"
export TRAILCAM_DATABASE_URL="sqlite+aiosqlite:///$WORK/demo.db"
export TRAILCAM_STATIC_DIR="$ROOT/frontend/dist"
export TRAILCAM_S3_ENDPOINT="http://127.0.0.1:$MOTO_PORT"
export TRAILCAM_S3_REGION=us-east-1
export TRAILCAM_S3_BUCKET=trailcam
export TRAILCAM_S3_ACCESS_KEY=demo
export TRAILCAM_S3_SECRET_KEY=demo

PIDS=()
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

echo "== moto S3 on :$MOTO_PORT"
uvx --from 'moto[server]' moto_server -p "$MOTO_PORT" >"$WORK/moto.log" 2>&1 &
PIDS+=($!)

echo "== frontend build"
if [ ! -d "$ROOT/frontend/dist" ] || [ -n "${SHOTS_REBUILD:-}" ]; then
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund && npm run build)
fi

echo "== migrate"
(cd "$ROOT/backend" && uv run alembic upgrade head)

echo "== backend on :$PORT"
(cd "$ROOT/backend" && uv run uvicorn trailcam.main:app --port "$PORT") \
  >"$WORK/backend.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 60); do
  curl -fsS "http://localhost:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "http://localhost:$PORT/healthz" >/dev/null

echo "== seed demo data"
(cd "$ROOT/backend" && uv run --with pillow python "$ROOT/scripts/screenshots/seed.py")

echo "== capture"
if [ ! -d "$ROOT/scripts/screenshots/node_modules" ]; then
  (cd "$ROOT/scripts/screenshots" && npm install --no-audit --no-fund && npx playwright install chromium)
fi
(cd "$ROOT/scripts/screenshots" && TRAILCAM_APP="http://localhost:$PORT" node capture.mjs "$@")

echo "== done: $WORK/png/"
ls -la "$WORK/png/"
