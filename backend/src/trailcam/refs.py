"""Generous UUID resolution for user-facing ids (docker/git short-hash style).

Everything a person sees (URLs, API payloads) identifies rows by UUID, never by
the int PK — ints stay internal for cheap FK joins. A "ref" in a path or query
param accepts either a full UUID (dashes optional, case-insensitive) or an
unambiguous hex prefix of at least MIN_PREFIX_HEX chars: 404 when nothing
matches, 409 when the prefix matches more than one row.

Resolution stays index-friendly on both backends: a hex prefix maps to an
inclusive [lo, hi] UUID range (pad with 0s / fs), which the unique index can
range-scan — Postgres orders native uuids bytewise and SQLite stores them as
lowercase hex CHAR(32), so both collate in hex order.
"""

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

# Short enough to type from a neighbor's screen, long enough that a stale
# pre-UUID integer URL (/nodes/5) can't silently resolve to the wrong row.
MIN_PREFIX_HEX = 8

_HEX = set("0123456789abcdef")


def uuid_ref_bounds(ref: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Full UUID or hex prefix -> inclusive UUID range; None when malformed."""
    hexstr = ref.replace("-", "").lower()
    if not (MIN_PREFIX_HEX <= len(hexstr) <= 32) or not set(hexstr) <= _HEX:
        return None
    return uuid.UUID(hexstr.ljust(32, "0")), uuid.UUID(hexstr.ljust(32, "f"))


async def resolve_uuid_ref(
    session: AsyncSession,
    query: Select,
    column: InstrumentedAttribute,
    ref: str,
    what: str,
) -> Any:
    """Resolve `ref` against `column` within `query`, returning the single row."""
    bounds = uuid_ref_bounds(ref)
    if bounds is None:
        raise HTTPException(404, f"{what} not found")
    rows = (await session.scalars(query.where(column.between(*bounds)).limit(2))).all()
    if not rows:
        raise HTTPException(404, f"{what} not found")
    if len(rows) > 1:
        raise HTTPException(409, f"ambiguous {what} id prefix")
    return rows[0]
