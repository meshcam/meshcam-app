"""Re-identification: telling a human that a node came back under a new name.

A leaf's wire slug is derived from its RNS identity. Lose the identity and the
same physical board reappears under a new slug, auto-creating a second camera
row and splitting its history in two. Since leaf-0.21.0 the board
notices this itself — it keeps an identity witness on LittleFS, and when the
derived identity stops matching it, it tags its next few announces with
`x=<previous-slug>`. gateway-0.19.0 forwards that as `extra.prev_node` on the
telemetry beat, so the server has always RECORDED the event. Nothing showed it
to anyone: you had to query `telemetry.extra` to know a merge was owed.

Two rules do the work here, and both are deliberate:

**Which beat counts.** The newest telemetry row *that carries* `prev_node` —
not the newest row. The leaf tags only a short burst of announces after a
re-identification (twelve, on the 09-05 bench run) and then goes quiet about
it forever, so reading `latest.extra` would show the badge for a few minutes
and then hide the very thing it exists to report. There is no recency window
either, unlike `extra.health` and `extra.sd`: a stale "pushes failing" is
noise, but a merge that was owed six weeks ago is still owed. The flag ages
out on its own when the evidence does, at settings.telemetry_ttl_days.

**When it clears.** Resolve the reported slug inside the node's site the same
way ingest does — a camera under that slug, else a camera aliased to it — and
compare it to the node reporting it. If it resolves to this same row, the two
halves are already one and there is nothing to show; that covers both merge
directions, because keeping the old row leaves the node sitting on a row whose
slug IS the reported one, and keeping the new row leaves it holding the old
slug as an alias. It also covers a chain of merges, and a leaf that reports
its own current slug. Anything else is a split that no one has repaired yet,
and the row it resolves to (when one exists) is the row holding the other half
— which is the merge target, not a coincidence.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trailcam.models import Camera, CameraAlias


def prev_node_from_extra(extra: dict | None) -> str | None:
    """The retired slug a telemetry beat reports, or None if it reports none.

    Anything that is not a non-empty string is None: `extra` is whatever the
    gateway posted, and this is read on every health poll."""
    if not isinstance(extra, dict):
        return None
    value = extra.get("prev_node")
    if not isinstance(value, str):
        return None
    return value.strip() or None


async def unmerged_prev_nodes(
    session: AsyncSession,
    nodes: Sequence[Camera],
    reported: dict[int, str],
) -> dict[int, tuple[str, uuid.UUID | None]]:
    """Which reported re-identifications still need a merge.

    `reported` maps camera id -> the slug that camera's newest tagged beat
    named. The result keeps only the ones that are still split, mapping camera
    id -> (retired slug, public id of the row holding the other half). That
    second value is None when nothing answers to the slug any more — the
    history it names is gone, so there is nothing to merge into, but the fact
    that this board changed identity is still worth saying out loud."""
    if not reported:
        return {}
    by_id = {n.id: n for n in nodes}
    by_slug = {(n.site_id, n.slug): n for n in nodes}

    slugs = {slug for cid, slug in reported.items() if cid in by_id}
    alias_target: dict[tuple[int, str], int] = {}
    if slugs:
        rows = await session.execute(
            select(CameraAlias.site_id, CameraAlias.slug, CameraAlias.camera_id).where(
                CameraAlias.slug.in_(slugs)
            )
        )
        alias_target = {(site_id, slug): cam_id for site_id, slug, cam_id in rows.all()}

    out: dict[int, tuple[str, uuid.UUID | None]] = {}
    for cam_id, slug in reported.items():
        node = by_id.get(cam_id)
        if node is None:
            continue
        key = (node.site_id, slug)
        target = by_slug.get(key)
        target_id = target.id if target is not None else alias_target.get(key)
        if target_id == node.id:
            continue  # already merged, in either direction — nothing owed
        holder = by_id.get(target_id) if target_id is not None else None
        out[cam_id] = (slug, holder.public_id if holder is not None else None)
    return out
