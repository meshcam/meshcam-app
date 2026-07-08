"""Thin async S3 wrapper. Backend is Garage in-cluster today; the S3 API is the
portability line if this ever becomes a hosted product (swap endpoint for real S3/R2)."""

from collections.abc import AsyncIterator

import aioboto3

from trailcam.config import get_settings


def raw_variant(key: str) -> str:
    """Sibling key holding the pristine (pre-OSD-stamp) bytes of a full image
    (see trailcam.osd). Pure naming convention — no DB column needed."""
    return key.removesuffix(".jpg") + ".raw.jpg"


class S3Store:
    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.s3_bucket
        self._session = aioboto3.Session()
        self._client_kwargs = dict(
            service_name="s3",
            endpoint_url=s.s3_endpoint,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
        )

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        async with self._session.client(**self._client_kwargs) as c:
            await c.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    async def get(self, key: str) -> bytes | None:
        """Whole object, or None if it doesn't exist (unlike stream(), which
        raises lazily mid-response)."""
        async with self._session.client(**self._client_kwargs) as c:
            try:
                obj = await c.get_object(Bucket=self.bucket, Key=key)
            except c.exceptions.NoSuchKey:
                return None
            return await obj["Body"].read()

    async def stream(self, key: str, chunk_size: int = 1 << 16) -> AsyncIterator[bytes]:
        async with self._session.client(**self._client_kwargs) as c:
            obj = await c.get_object(Bucket=self.bucket, Key=key)
            async for chunk in obj["Body"].iter_chunks(chunk_size):
                yield chunk

    async def delete(self, keys: list[str]) -> None:
        if not keys:
            return
        async with self._session.client(**self._client_kwargs) as c:
            await c.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
            )


_store: S3Store | None = None


def get_store() -> S3Store:
    global _store
    if _store is None:
        _store = S3Store()
    return _store
