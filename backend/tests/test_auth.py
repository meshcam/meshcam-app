import pytest
from httpx import ASGITransport, AsyncClient

import trailcam.db as dbmod
from trailcam.auth import current_user, hash_token, mint_token, oauth
from trailcam.config import Settings, get_settings
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
    assert r.json() == {
        "email": "test@example.com",
        "name": "Test",
        "demo": False,
        "map_tile_url": "",
        "map_tile_attribution": "",
    }


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
    assert r.json() == {
        "email": "demo@getmeshcam.com",
        "name": "Demo",
        "demo": True,
        "map_tile_url": "",
        "map_tile_attribution": "",
    }


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


# --- Login allowlist (TRAILCAM_ALLOWED_EMAILS) --------------------------------


def test_allowed_emails_set_parses_and_normalizes():
    s = Settings(allowed_emails=" Alice@Example.com, bob@example.com ,, ")
    assert s.allowed_emails_set == {"alice@example.com", "bob@example.com"}


def test_allowed_emails_set_empty_by_default():
    assert Settings(allowed_emails="").allowed_emails_set == set()


@pytest.fixture()
def allowed_emails(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setenv("TRAILCAM_ALLOWED_EMAILS", value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()  # restore the no-allowlist default for later tests


def _fake_oidc_login(email: str, name: str = "Someone"):
    async def _authorize_access_token(request):
        return {"userinfo": {"email": email, "name": name}}

    return _authorize_access_token


@pytest.fixture()
async def real_auth_client(store):
    # Mirrors conftest's `app`/`client` fixtures but WITHOUT the current_user
    # override — these tests exercise the real /auth/callback session-creation
    # path, with only oauth.oidc.authorize_access_token faked out.
    if dbmod._engine is not None:
        await dbmod._engine.dispose()
    dbmod._engine = None
    dbmod._sessionmaker = None
    application = create_app()
    async with dbmod.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_callback_admits_any_email_when_allowlist_unset(monkeypatch, real_auth_client):
    login = _fake_oidc_login("anyone@example.com")
    monkeypatch.setattr(oauth.oidc, "authorize_access_token", login)
    r = await real_auth_client.get("/auth/callback")
    assert r.status_code in (302, 307)
    me = await real_auth_client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["email"] == "anyone@example.com"


async def test_callback_rejects_email_not_in_allowlist(
    monkeypatch, allowed_emails, real_auth_client
):
    allowed_emails("alice@example.com,bob@example.com")
    login = _fake_oidc_login("mallory@example.com")
    monkeypatch.setattr(oauth.oidc, "authorize_access_token", login)
    r = await real_auth_client.get("/auth/callback")
    assert r.status_code == 403
    # No session was established — the callback rejected before writing one.
    assert (await real_auth_client.get("/api/v1/me")).status_code == 401


async def test_callback_admits_email_in_allowlist_case_insensitive(
    monkeypatch, allowed_emails, real_auth_client
):
    allowed_emails("alice@example.com")
    monkeypatch.setattr(
        oauth.oidc, "authorize_access_token", _fake_oidc_login("Alice@Example.com", name="Alice")
    )
    r = await real_auth_client.get("/auth/callback")
    assert r.status_code in (302, 307)
    me = await real_auth_client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["email"] == "Alice@Example.com"
