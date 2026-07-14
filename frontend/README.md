# trailcam-frontend

Web UI for the MeshCam trail-camera mesh. React 19 + TypeScript + Vite,
plain handwritten CSS (no UI framework, no router; single view + detail
overlay). Cookie-session auth against the backend API (same origin in prod).

## Develop

```sh
npm install
npm run dev     # Vite dev server; proxies /api and /auth -> http://localhost:8000
```

## Build

```sh
npm run build   # tsc -b (strict) + vite build -> dist/
npm run lint    # oxlint
```
