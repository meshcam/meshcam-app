"""Settings-page support: device token management + retention stats.

Anyone past the OIDC allowlist is family, so there is no separate admin role —
the same session that can delete photos can mint gateway tokens. Tokens are
returned in plaintext exactly once, at creation; only the sha256 is stored.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.auth import current_user, hash_token, mint_token
from trailcam.config import get_settings
from trailcam.db import get_session
from trailcam.models import DeviceToken, Photo, utcnow
from trailcam.refs import resolve_uuid_ref
from trailcam.schemas import (
    DeviceTokenCreatedOut,
    DeviceTokenCreateIn,
    DeviceTokenOut,
    RetentionStats,
)

router = APIRouter(prefix="/api/v1", tags=["settings"], dependencies=[Depends(current_user)])

EXPIRING_SOON_DAYS = 7


def _token_out(t: DeviceToken) -> DeviceTokenOut:
    return DeviceTokenOut(
        id=t.public_id, name=t.name, created_at=t.created_at, last_used_at=t.last_used_at
    )


@router.get("/device-tokens", response_model=list[DeviceTokenOut])
async def list_device_tokens(session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(DeviceToken).order_by(DeviceToken.name))).all()
    return [_token_out(t) for t in rows]


@router.post("/device-tokens", response_model=DeviceTokenCreatedOut, status_code=201)
async def create_device_token(
    body: DeviceTokenCreateIn, session: AsyncSession = Depends(get_session)
):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name must not be empty")
    exists = await session.scalar(select(DeviceToken).where(DeviceToken.name == name))
    if exists is not None:
        raise HTTPException(409, f"a device token named {name!r} already exists")
    token = mint_token()
    row = DeviceToken(name=name, token_hash=hash_token(token))
    session.add(row)
    await session.commit()
    return DeviceTokenCreatedOut(
        id=row.public_id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        token=token,
    )


@router.delete("/device-tokens/{token_ref}", status_code=204)
async def revoke_device_token(token_ref: str, session: AsyncSession = Depends(get_session)):
    row = await resolve_uuid_ref(
        session, select(DeviceToken), DeviceToken.public_id, token_ref, "device token"
    )
    await session.delete(row)
    await session.commit()


@router.get("/retention", response_model=RetentionStats)
async def retention_stats(session: AsyncSession = Depends(get_session)):
    now = utcnow()
    total, kept, thumb_bytes, full_bytes = (
        await session.execute(
            select(
                func.count(Photo.id),
                func.count(Photo.id).filter(Photo.keep.is_(True)),
                func.coalesce(func.sum(Photo.thumb_size), 0),
                func.coalesce(func.sum(Photo.full_size), 0),
            )
        )
    ).one()
    expiring = await session.scalar(
        select(func.count(Photo.id)).where(
            Photo.keep.is_(False),
            Photo.expires_at.is_not(None),
            Photo.expires_at <= now + timedelta(days=EXPIRING_SOON_DAYS),
        )
    )
    return RetentionStats(
        ttl_days=get_settings().photo_ttl_days,
        photo_count=total,
        kept_count=kept,
        expiring_soon=expiring or 0,
        thumb_bytes=thumb_bytes,
        full_bytes=full_bytes,
    )
