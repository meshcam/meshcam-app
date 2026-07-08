"""Auth: OIDC (your OIDC provider) session cookies for humans, bearer tokens for devices.

Humans: classic confidential-client code flow (BFF style). The IdP's user
allowlist is the whole access model — anyone who can get a token is family.
Devices (the mesh gateway): static bearer tokens, sha256
stored in device_tokens, minted with `python -m trailcam.devicetoken <name>`.
"""

import hashlib
import os
import secrets

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.config import get_settings
from trailcam.db import get_session
from trailcam.models import DeviceToken, utcnow

TOKEN_PREFIX = "tc_"

oauth = OAuth()


def init_oauth() -> None:
    settings = get_settings()
    oauth.register(
        name="oidc",
        server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        # your OIDC provider requires PKCE even for confidential clients.
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request, next: str = "/"):
    if not next.startswith("/"):
        next = "/"
    request.session["next"] = next
    redirect_uri = f"{get_settings().public_url}/auth/callback"
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    try:
        token = await oauth.oidc.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(401, f"OIDC error: {exc.error}") from exc
    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    if not email:
        raise HTTPException(401, "No email in OIDC claims")
    request.session["user"] = {"email": email, "name": userinfo.get("name") or email}
    return RedirectResponse(request.session.pop("next", "/"))


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/screenshot-login")
async def screenshot_login(request: Request):
    """Mint a session for the screenshot pipeline (scripts/screenshots/) without
    OIDC. Requires BOTH env=dev and an explicit TRAILCAM_SCREENSHOT_LOGIN=1 —
    the deployed dev overlay runs env=dev on a reachable URL, so the flag keeps
    this a local-runner-only door."""
    if get_settings().env != "dev" or os.environ.get("TRAILCAM_SCREENSHOT_LOGIN") != "1":
        raise HTTPException(404, "Not found")
    request.session["user"] = {"email": "demo@montmere.com", "name": "Demo"}
    return {"ok": True}


def current_user(request: Request) -> dict:
    # A real OIDC session always wins — demo mode NEVER shadows or bypasses a
    # signed-in family member (they keep their own identity and full access).
    user = request.session.get("user")
    if user:
        return user
    # No session: on the public read-only demo instance, open reads anonymously
    # via a synthetic user. Writes are still impossible — main.py's method
    # middleware 403s every /api/ mutation regardless of this user. Off the demo
    # instance (dev/prod) demo_mode is False, so this stays a hard 401.
    if get_settings().demo_mode:
        return {"email": "demo@getmeshcam.com", "name": "Demo", "demo": True}
    raise HTTPException(401, "Not signed in")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def mint_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


async def current_device(
    request: Request, session: AsyncSession = Depends(get_session)
) -> DeviceToken:
    authz = request.headers.get("authorization", "")
    if not authz.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authz.removeprefix("Bearer ").strip()
    if not token.startswith(TOKEN_PREFIX):
        raise HTTPException(401, "Bad token")
    row = await session.scalar(
        select(DeviceToken).where(DeviceToken.token_hash == hash_token(token))
    )
    if row is None:
        raise HTTPException(401, "Unknown token")
    row.last_used_at = utcnow()
    await session.commit()
    return row
