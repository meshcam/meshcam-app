import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import Scope

from trailcam import auth
from trailcam.api import cameras, commands, ingest, photos, stream, telemetry
from trailcam.api import settings as settings_api
from trailcam.config import get_settings
from trailcam.db import get_engine

logger = logging.getLogger("trailcam")

# Methods that only read; anything else against /api/ is a mutation.
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Demo-mode write exceptions. The demo stopped being a museum in 2026-07: a
# simulated mesh (trailcam.meshsim) runs against the same instance, so the
# device-facing API must accept writes — those routes all require a device
# bearer token, which anonymous visitors don't have. request-full is the ONE
# anonymous write we allow: it's the product's signature interaction, it only
# queues a bounded Command row (dedup + cap in the endpoint), and the
# simulator is the thing that acts on it.
_DEMO_DEVICE_PREFIXES = ("/api/v1/ingest", "/api/v1/telemetry", "/api/v1/commands")


def _demo_write_allowed(method: str, path: str) -> bool:
    if method != "POST":
        return False
    if path.startswith(_DEMO_DEVICE_PREFIXES):
        return True  # device-token routes enforce their own auth
    return path.startswith("/api/v1/photos/") and path.endswith("/request-full")


class SPAStaticFiles(StaticFiles):
    """Static files with SPA fallback: unknown non-API paths serve index.html so
    client routes (/photos/<id>, /nodes/...) deep-link and refresh cleanly.
    API/auth 404s stay 404s (routers match first; this guard is belt+braces).

    Cache policy: index.html must revalidate every load (it's the deploy
    pointer — stale copies pin users to old bundles), while Vite's
    content-hashed /assets/* are immutable."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and not scope["path"].startswith(("/api/", "/auth/")):
                response = await super().get_response("index.html", scope)
                path = "index.html"
            else:
                raise
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path in ("index.html", "."):
            response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().demo_mode:
        # Loud, so a misconfigured dev/prod instance running with this flag is
        # obvious in the logs — it means the login wall is off for everyone.
        logger.warning(
            "DEMO MODE ENABLED: login wall is OFF (anonymous reads); /api/ "
            "writes are blocked except device-token routes (the simulated "
            "mesh) and request-full (capped). This must ONLY run on the "
            "public demo deployment, never dev or prod."
        )
    get_engine()
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="trailcam", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        max_age=settings.session_max_age,
        https_only=settings.public_url.startswith("https://"),
        same_site="lax",
    )

    @app.middleware("http")
    async def block_writes_in_demo(request: Request, call_next):
        # THE write-blocker for the public demo. Method-based (not per-route) so
        # any future mutating /api/ route is covered automatically — a request
        # that isn't a read against /api/ can never mutate on the demo instance.
        # get_settings() is read here at request time (it's lru_cached), so the
        # check reflects the running config, not import order.
        if (
            get_settings().demo_mode
            and request.method not in _READ_METHODS
            and request.url.path.startswith("/api/")
            and not _demo_write_allowed(request.method, request.url.path)
        ):
            return JSONResponse({"detail": "read-only demo"}, status_code=403)
        return await call_next(request)

    auth.init_oauth()
    app.include_router(auth.router)
    app.include_router(ingest.router)
    app.include_router(photos.router)
    app.include_router(cameras.router)
    app.include_router(telemetry.ingest_router)
    app.include_router(telemetry.nodes_router)
    app.include_router(commands.router)
    app.include_router(stream.router)
    app.include_router(settings_api.router)

    @app.get("/healthz")
    async def healthz():
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": True}

    static = Path(settings.static_dir)
    if static.is_dir():
        app.mount("/", SPAStaticFiles(directory=static, html=True), name="static")
    return app


app = create_app()
