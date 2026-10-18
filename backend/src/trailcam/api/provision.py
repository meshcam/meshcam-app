"""Machine provisioning: mint and revoke this instance's device tokens.

The sibling of `api/settings.py`'s device-token routes. Same rows, same
one-plaintext-view rule, different caller: settings.py is a signed-in person
clicking a button, this is a program with a shared key.

It exists because of how a gateway gets claimed. A factory-fresh gateway knows
one URL, a cloud control plane, and nothing about which account it belongs to.
The claim exchange hands it back three values at once — site slug, ingest URL,
and a device token — and the token has to be a row in THIS instance's
device_tokens table, because that is the only table its ingest checks. So the
control plane calls in here rather than reaching into the database, which is
what keeps "your data is in its own database" true.

Self-hosters get the same door for free: TRAILCAM_PROVISION_KEY is how you mint
a gateway token from a script instead of `kubectl exec`. Leave it unset and
these routes do not exist.

Scope, stated because it is not obvious: a device token is NOT site-scoped.
`site_slug` here only decides which node rows get pre-created. Any valid token
can ingest to any site on this instance, which is fine while one instance means
one account, and would need a real scope column the day that stops being true.
"""

import logging
import re
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.auth import hash_token, mint_token
from trailcam.config import get_settings
from trailcam.crud import get_or_create_node
from trailcam.db import get_session
from trailcam.models import DeviceToken
from trailcam.refs import resolve_uuid_ref
from trailcam.schemas import DeviceTokenCreatedOut, ProvisionTokenIn

logger = logging.getLogger("trailcam")

# Machine ids, not human names: `gw-3fa2b1c4`, `bench-gateway-2`. Deliberately
# narrower than the settings-page route, which lets a person type anything.
# This one is only ever called by a program, so a name it cannot generate is a
# bug worth surfacing rather than a preference worth honouring.
NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SLUG_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")

# Nothing below normalizes beyond stripping whitespace, and that is deliberate.
# `POST /ingest` stores whatever site slug the gateway sends, verbatim, and
# auto-creates on miss. So if this route lowercased `Smith-Ranch` to
# `smith-ranch` while the control plane handed the gateway the original, the
# first photo would quietly land in a SECOND site. On a machine API the right
# answer to a non-canonical slug is 422, not a silent fix.


async def require_provision_key(x_provision_key: str = Header(default="")) -> None:
    """Gate every route below on the shared key.

    A disabled instance answers 404, not 401 or 403. An instance that has never
    set the key should look like an instance without the feature, so scanning
    for it tells an attacker nothing. A wrong key on an ENABLED instance is a
    401, because at that point the route's existence is already established by
    the operator who turned it on, and a caller with a stale key needs to be
    able to tell that apart from a typo'd URL.
    """
    settings = get_settings()
    if not settings.provision_enabled:
        raise HTTPException(404, "Not found")
    if not secrets.compare_digest(x_provision_key, settings.provision_key):
        raise HTTPException(401, "Bad provision key")


router = APIRouter(
    prefix="/api/v1/provision",
    tags=["provision"],
    dependencies=[Depends(require_provision_key)],
)


@router.post("/device-tokens", response_model=DeviceTokenCreatedOut, status_code=201)
async def provision_device_token(
    body: ProvisionTokenIn, session: AsyncSession = Depends(get_session)
):
    name = body.name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(422, "name must be 1-64 chars of [A-Za-z0-9._-]")
    site_slug = (body.site_slug or "").strip()
    if site_slug and not SLUG_RE.match(site_slug):
        raise HTTPException(422, "site_slug must be 1-64 chars of [a-z0-9-]")

    existing = await session.scalar(select(DeviceToken).where(DeviceToken.name == name))
    if existing is not None:
        if not body.replace:
            raise HTTPException(409, f"a device token named {name!r} already exists")
        # Revoke-then-mint, flushed between, or the unique name collides inside
        # the transaction. The old token stops working the moment this commits,
        # which is the point: a re-claim must not leave a second live credential
        # for the same unit lying around.
        await session.delete(existing)
        await session.flush()

    token = mint_token()
    row = DeviceToken(name=name, token_hash=hash_token(token))
    session.add(row)
    await session.flush()

    if site_slug:
        # Pre-create the site and the gateway node so the account is not an
        # empty page between "claimed" and the first heartbeat.
        await get_or_create_node(session, site_slug, name, kind="gateway")

    await session.commit()
    # The token itself is never logged, here or anywhere. The public id is what
    # ties a support question to a row.
    logger.info(
        "provisioned device token name=%s id=%s site=%s replaced=%s",
        name,
        row.public_id,
        site_slug or "-",
        existing is not None,
    )
    return DeviceTokenCreatedOut(
        id=row.public_id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        token=token,
    )


@router.delete("/device-tokens/{token_ref}", status_code=204)
async def revoke_device_token(token_ref: str, session: AsyncSession = Depends(get_session)):
    """Revoke by public id.

    404 when the row is not there. Callers doing an RMA release should treat
    404 as success: the token being absent IS the desired end state, and the
    owner may have already revoked it from the settings page.
    """
    row = await resolve_uuid_ref(
        session, select(DeviceToken), DeviceToken.public_id, token_ref, "device token"
    )
    name, public_id = row.name, row.public_id
    await session.delete(row)
    await session.commit()
    logger.info("revoked device token name=%s id=%s", name, public_id)
