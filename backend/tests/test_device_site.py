from sqlalchemy import update

from trailcam.db import get_sessionmaker
from trailcam.models import Site


async def test_site_autocreates_and_returns_name(client, device_token):
    r = await client.get(
        "/api/v1/site",
        params={"slug": "home"},
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "home"
    assert body["name"] == "Home"  # slug titlecased on autocreate


async def test_site_rename_trickles_through(client, device_token):
    hdr = {"Authorization": f"Bearer {device_token}"}
    await client.get("/api/v1/site", params={"slug": "north40"}, headers=hdr)
    # a rename in the settings UI is a plain Site.name update
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Site).where(Site.slug == "north40").values(name="North Forty")
        )
        await session.commit()
    r = await client.get("/api/v1/site", params={"slug": "north40"}, headers=hdr)
    assert r.json()["name"] == "North Forty"  # bubbles up, slug unchanged


async def test_site_requires_device_token(client):
    r = await client.get("/api/v1/site", params={"slug": "home"})
    assert r.status_code == 401
