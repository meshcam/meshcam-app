import pytest

import trailcam.db as dbmod
from trailcam.auth import current_user, hash_token, mint_token
from trailcam.config import get_settings
from trailcam.main import create_app
from trailcam.models import Base

JPEG = b"\xff\xd8\xff\xe0fake"


def test_mint_token_format_and_hash():
    t1, t2 = mint_token(), mint_token()
    assert t1.startswith("tc_") and t1 != t2
    assert len(hash_token(t1)) == 64
    assert hash_token(t1) == hash_token(t1)


async def test_unknown_device_token_rejected(client):
    r = await client.post(
        "/api/v1/ingest",
        data={"site": "s", "camera": "c", "event_id": "e", "captured_at": "2026-07-02T12:00:00Z"},
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {mint_token()}"},
    )
    assert r.status_code == 401


async def test_photos_requires_session(app, client):
    app.dependency_overrides.pop(current_user)
    assert (await client.get("/api/v1/photos")).status_code == 401
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_me(client):
    r = await client.get("/api/v1/me")
    assert r.status_code == 200
    assert r.json() == {"email": "test@example.com", "name": "Test", "demo": False}


# --- Public read-only demo mode ------------------------------------------------
#
# These run the REAL anonymous auth path (no current_user override) against a
# fresh app built with TRAILCAM_DEMO_MODE on, so they exercise both the synthetic
# demo user (auth.py) and the method-based write-blocker middleware (main.py).


@pytest.fixture()
def demo_settings(monkeypatch):
    monkeypatch.setenv("TRAILCAM_DEMO_MODE", "1")
    get_settings.cache_clear()  # drop the cached non-demo Settings
    yield
    get_settings.cache_clear()  # and reset so later tests see demo off again


@pytest.fixture()
async def demo_app(store, demo_settings):
    # Mirrors conftest's `app` fixture but with demo_mode on and NO current_user
    # override — the anonymous synthetic-user path must actually run.
    if dbmod._engine is not None:
        await dbmod._engine.dispose()
    dbmod._engine = None
    dbmod._sessionmaker = None
    application = create_app()
    async with dbmod.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return application


@pytest.fixture()
async def demo_client(demo_app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=demo_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_demo_me_is_anonymous_and_flagged(demo_client):
    # No session cookie at all — demo mode opens /me via the synthetic user.
    r = await demo_client.get("/api/v1/me")
    assert r.status_code == 200
    assert r.json() == {"email": "demo@getmeshcam.com", "name": "Demo", "demo": True}


async def test_demo_reads_work_anonymously(demo_client):
    # A representative GET succeeds with no login (empty DB → empty list).
    r = await demo_client.get("/api/v1/sites")
    assert r.status_code == 200
    assert r.json() == []


async def test_demo_blocks_writes(demo_client):
    # The middleware 403s every non-read /api/ request before the route runs.
    r = await demo_client.post("/api/v1/device-tokens", json={"name": "x"})
    assert r.status_code == 403
    assert r.json() == {"detail": "read-only demo"}

    r = await demo_client.patch("/api/v1/cameras/whatever", json={"name": "x"})
    assert r.status_code == 403

    r = await demo_client.post("/api/v1/photos/whatever/keep", json={"keep": True})
    assert r.status_code == 403
