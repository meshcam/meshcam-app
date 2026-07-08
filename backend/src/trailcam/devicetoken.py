"""Mint a device (ingest) bearer token: `python -m trailcam.devicetoken <name>`.

Prints the token ONCE; only the sha256 lands in the DB. Run inside a deployed
pod so it hits the right database:
  kubectl -n trailcam exec deploy/trailcam -- python -m trailcam.devicetoken site1-gateway
"""

import asyncio
import sys

from trailcam.auth import hash_token, mint_token
from trailcam.db import get_sessionmaker
from trailcam.models import DeviceToken


async def main(name: str) -> None:
    token = mint_token()
    async with get_sessionmaker()() as session:
        session.add(DeviceToken(name=name, token_hash=hash_token(token)))
        await session.commit()
    print(f"device token for {name!r} (save it now, it is not stored):\n{token}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m trailcam.devicetoken <device-name>")
    asyncio.run(main(sys.argv[1]))
