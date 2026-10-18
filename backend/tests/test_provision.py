"""Machine provisioning door: /api/v1/provision/device-tokens.

The thing under test is mostly the gate, not the mint. Minting is already
covered by test_settings.py's device-token lifecycle; what is new here is that
an internet-reachable route can create a working credential, so every way of
reaching it without the key has to fail, and a disabled instance has to look
like an instance that never had the route.
"""

import pytest
from httpx import ASGITransport, AsyncClient

import trailcam.db as dbmod
from trailcam.config import get_settings
from trailcam.main import create_app
from trailcam.models import Base, utcnow

JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"
KEY = "k" * 40


async def ingest(client, token, event_id="evt-1", site="home", camera="c1"):
    return await client.post(
        "/api/v1/ingest",
        data={
            "site": site,
            "camera": camera,
            "event_id": event_id,
            "captured_at": utcnow().isoformat(),
            "kind": "thumb",
        },
        files={"file": ("x.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture()
def provision_key(monkeypatch):
    """Enable the door. Listed BEFORE `app` wherever it is used, so the setting
    is in place before create_app() reads it."""
    monkeypatch.setenv("TRAILCAM_PROVISION_KEY", KEY)
    get_settings.cache_clear()
    yield KEY
    get_settings.cache_clear()


@pytest.fixture()
async def papp(provision_key, app):
    return app


@pytest.fixture()
async def pclient(papp):
    transport = ASGITransport(app=papp)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def hdr(key=KEY):
    return {"X-Provision-Key": key}


async def mint(client, name="gw-3fa2b1c4", key=KEY, **body):
    return await client.post(
        "/api/v1/provision/device-tokens", json={"name": name, **body}, headers=hdr(key)
    )


# --- the gate ----------------------------------------------------------------


async def test_disabled_instance_has_no_such_route(client):
    """conftest never sets TRAILCAM_PROVISION_KEY, so this is the self-hoster
    default. 404, not 401 — probing tells you nothing."""
    r = await client.post("/api/v1/provision/device-tokens", json={"name": "gw-1"})
    assert r.status_code == 404
    r = await client.delete("/api/v1/provision/device-tokens/" + "0" * 32)
    assert r.status_code == 404
    # Even with a header. There is no key that opens a door that is not there.
    r = await mint(client)
    assert r.status_code == 404


async def test_short_key_fails_closed(monkeypatch, store):
    """A placeholder or a typo must not open a mint-a-credential route."""
    monkeypatch.setenv("TRAILCAM_PROVISION_KEY", "short")
    get_settings.cache_clear()
    try:
        assert get_settings().provision_enabled is False
    finally:
        get_settings.cache_clear()


async def test_wrong_and_missing_key_rejected(pclient):
    r = await pclient.post("/api/v1/provision/device-tokens", json={"name": "gw-1"})
    assert r.status_code == 401
    r = await mint(pclient, key="k" * 39 + "x")
    assert r.status_code == 401
    r = await mint(pclient, key="")
    assert r.status_code == 401


# --- minting -----------------------------------------------------------------


async def test_minted_token_ingests(pclient):
    r = await mint(pclient)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["token"].startswith("tc_")
    assert created["name"] == "gw-3fa2b1c4"
    assert created["last_used_at"] is None

    assert (await ingest(pclient, created["token"])).status_code == 200

    # The plaintext never comes back a second time, on any surface.
    listed = (await pclient.get("/api/v1/device-tokens")).json()
    ours = next(t for t in listed if t["name"] == "gw-3fa2b1c4")
    assert "token" not in ours
    assert ours["id"] == created["id"]


async def test_duplicate_name_conflicts_unless_replacing(pclient):
    first = (await mint(pclient)).json()
    r = await mint(pclient)
    assert r.status_code == 409

    r = await mint(pclient, replace=True)
    assert r.status_code == 201, r.text
    second = r.json()
    assert second["token"] != first["token"]
    assert second["id"] != first["id"]

    # A re-claim must not leave two live credentials for one unit.
    assert (await ingest(pclient, second["token"], event_id="e2")).status_code == 200
    assert (await ingest(pclient, first["token"], event_id="e3")).status_code == 401

    listed = (await pclient.get("/api/v1/device-tokens")).json()
    assert [t["name"] for t in listed].count("gw-3fa2b1c4") == 1


async def test_site_slug_precreates_the_gateway_node(pclient):
    r = await mint(pclient, site_slug="smith-ranch")
    assert r.status_code == 201, r.text

    sites = (await pclient.get("/api/v1/sites")).json()
    assert [s["slug"] for s in sites] == ["smith-ranch"]

    cams = (await pclient.get("/api/v1/cameras")).json()
    gw = next(c for c in cams if c["slug"] == "gw-3fa2b1c4")
    assert gw["kind"] == "gateway"
    assert gw["site_slug"] == "smith-ranch"
    assert gw["last_seen_at"] is None  # nothing has beaten yet, and that is honest


async def test_site_slug_is_optional(pclient):
    assert (await mint(pclient)).status_code == 201
    assert (await pclient.get("/api/v1/sites")).json() == []


@pytest.mark.parametrize("name", ["", "   ", "-leading", "has space", "a" * 65, "semi;colon"])
async def test_bad_names_rejected(pclient, name):
    assert (await mint(pclient, name=name)).status_code == 422


@pytest.mark.parametrize("slug", ["Upper", "-leading", "under_score", "a" * 65, "sp ace"])
async def test_bad_site_slugs_rejected(pclient, slug):
    """`Upper` is in here on purpose. Lowercasing it would be the friendly
    thing and the wrong one: ingest stores site slugs verbatim, so a silently
    normalized slug means the first photo lands in a second site."""
    assert (await mint(pclient, site_slug=slug)).status_code == 422


# --- revocation --------------------------------------------------------------


async def test_revoke_kills_the_token(pclient):
    created = (await mint(pclient)).json()
    assert (await ingest(pclient, created["token"])).status_code == 200

    r = await pclient.delete(
        f"/api/v1/provision/device-tokens/{created['id']}", headers=hdr()
    )
    assert r.status_code == 204
    assert (await ingest(pclient, created["token"], event_id="e2")).status_code == 401


async def test_revoke_unknown_is_404(pclient):
    r = await pclient.delete("/api/v1/provision/device-tokens/" + "0" * 32, headers=hdr())
    assert r.status_code == 404


async def test_revoke_needs_the_key(pclient):
    created = (await mint(pclient)).json()
    r = await pclient.delete(f"/api/v1/provision/device-tokens/{created['id']}")
    assert r.status_code == 401
    # Still live.
    assert (await ingest(pclient, created["token"])).status_code == 200


# --- demo instance -----------------------------------------------------------


async def test_demo_mode_blocks_provisioning(monkeypatch, store):
    """The public demo must never mint a credential, key or no key. The
    method-based write blocker in main.py already covers this; the test is here
    because 'already covered' is a claim that rots."""
    monkeypatch.setenv("TRAILCAM_PROVISION_KEY", KEY)
    monkeypatch.setenv("TRAILCAM_DEMO_MODE", "1")
    get_settings.cache_clear()
    try:
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
            r = await c.post(
                "/api/v1/provision/device-tokens", json={"name": "gw-1"}, headers=hdr()
            )
            assert r.status_code == 403
    finally:
        get_settings.cache_clear()
