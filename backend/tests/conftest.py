import os
import tempfile

_dbdir = tempfile.mkdtemp(prefix="trailcam-test-")
_staticdir = tempfile.mkdtemp(prefix="trailcam-static-")
with open(f"{_staticdir}/index.html", "w") as _f:
    _f.write("<!doctype html><title>trailcam-test-spa</title>")
os.environ["TRAILCAM_ENV"] = "test"
os.environ["TRAILCAM_DATABASE_URL"] = f"sqlite+aiosqlite:///{_dbdir}/test.db"
os.environ["TRAILCAM_DB_HOST"] = ""
os.environ["TRAILCAM_PUBLIC_URL"] = "http://testserver"
os.environ["TRAILCAM_SESSION_SECRET"] = "test-secret"
os.environ["TRAILCAM_PHOTO_TTL_DAYS"] = "30"
os.environ["TRAILCAM_STATIC_DIR"] = _staticdir

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

import trailcam.db as dbmod  # noqa: E402
import trailcam.s3 as s3mod  # noqa: E402
from trailcam.auth import current_user, hash_token, mint_token  # noqa: E402
from trailcam.main import create_app  # noqa: E402
from trailcam.models import Base, DeviceToken  # noqa: E402


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = body

    async def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def stream(self, key: str, chunk_size: int = 1 << 16):
        yield self.objects[key]

    async def delete(self, keys: list[str]) -> None:
        for k in keys:
            self.objects.pop(k, None)


@pytest.fixture()
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(s3mod, "_store", fake)
    return fake


@pytest.fixture()
async def app(store):
    # Fresh engine per test — engines are bound to the running event loop.
    if dbmod._engine is not None:
        await dbmod._engine.dispose()
    dbmod._engine = None
    dbmod._sessionmaker = None
    application = create_app()
    async with dbmod.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    application.dependency_overrides[current_user] = lambda: {
        "email": "test@example.com",
        "name": "Test",
    }
    return application


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture()
async def device_token(app) -> str:
    token = mint_token()
    async with dbmod.get_sessionmaker()() as session:
        session.add(DeviceToken(name="test-device", token_hash=hash_token(token)))
        await session.commit()
    return token
