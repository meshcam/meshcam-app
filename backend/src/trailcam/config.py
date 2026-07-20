from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRAILCAM_", env_file=".env", extra="ignore")

    env: str = "dev"
    public_url: str = "http://localhost:8000"
    static_dir: str = "/app/static"

    # Database — either a full async URL, or CNPG-style parts (parts win when host is set).
    database_url: str = "sqlite+aiosqlite:///:memory:"
    db_host: str = ""
    db_port: int = 5432
    db_user: str = "trailcam"
    db_password: str = ""
    db_name: str = "trailcam"
    db_ssl: bool = True

    # Object storage (S3 API — Garage in-cluster; any S3 works).
    s3_endpoint: str = ""  # e.g. http://minio:9000 — any S3 API works
    s3_region: str = "garage"
    s3_bucket: str = "trailcam"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # OIDC — by default the IdP's own user list is the access control (anyone
    # who can get a token is family).
    oidc_issuer: str = ""  # e.g. https://auth.example.com
    oidc_client_id: str = "trailcam"
    oidc_client_secret: str = ""

    # Login allowlist / seats: comma-separated OIDC emails permitted to sign
    # in. Empty (the default) allows any authenticated email through — the
    # single-tenant, back-compat behavior above. Hosted multi-tenant
    # instances share one IdP, so this is what actually scopes an instance to
    # its account; the list length is the "seats" tier axis.
    allowed_emails: str = ""

    @property
    def allowed_emails_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    session_secret: str = "dev-only-not-a-secret"
    session_max_age: int = 30 * 24 * 3600  # 30d

    # SECURITY: Public read-only demo mode. When on, the login wall is removed
    # for the WHOLE instance — anonymous visitors get a synthetic read user and
    # every /api/ write is hard-blocked by method in the middleware (see main.py).
    # This must ONLY be set on the dedicated demo deployment (demo.getmeshcam.com);
    # NEVER set TRAILCAM_DEMO_MODE on dev or prod, or their data becomes world-readable.
    demo_mode: bool = False

    # OSD watermark (trailcam.osd): burn the info bar (camera/temp/battery/
    # timestamp) into every full-res image at ingest. The pristine bytes are
    # kept at a `.raw` sibling S3 key. osd_tz is the IANA zone the bar's
    # timestamp renders in ("" = UTC) — cameras don't know their timezone,
    # the deployment does.
    osd_stamp: bool = True
    osd_tz: str = ""

    # Survey-map basemap tiles. Empty (the default) renders a plain graticule
    # with a scale bar — relative geometry still works, which is enough for
    # antenna placement. This defaults OFF deliberately: probe coordinates are
    # precise wildlife-camera locations (poaching-sensitive for part of the
    # audience), and fetching third-party tiles leaks the survey area to that
    # party via the tile coordinates. Opting into OSM is a per-browser choice
    # in the settings UI; this env sets the deployment-wide default (the demo
    # overlay pins OSM — its property is fiction). URL template is Leaflet
    # style, e.g. https://tile.openstreetmap.org/{z}/{x}/{y}.png
    map_tile_url: str = ""
    map_tile_attribution: str = ""

    # Burst grouping: a capture-time gap longer than this (minutes, per
    # camera) starts a new sighting. Baked in at ingest — changing it
    # reshapes future photos only, existing groups keep their boundaries.
    # 30 matches the survey's session default and, on real field data,
    # collapsed 1731 photos into 91 scannable sightings (5 min only got 414).
    sighting_gap_min: int = 30

    # Retention: unsaved photos expire this many days after arrival.
    photo_ttl_days: int = 180
    # Telemetry heartbeats age out after this many days (health history only).
    telemetry_ttl_days: int = 180
    # Outstanding node commands (e.g. fetch_full) expire after this many days.
    # Even the sparsest field schedule (2 h night check-ins) gives a command ~12
    # delivery chances in a day; past that it's re-announce noise.
    command_ttl_days: int = 1

    @property
    def sqlalchemy_url(self) -> str:
        if self.db_host:
            ssl = "?ssl=require" if self.db_ssl else ""
            return (
                f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}{ssl}"
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
