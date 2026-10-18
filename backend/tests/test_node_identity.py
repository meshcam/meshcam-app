"""Surfacing a re-identification to a human.

`test_camera_merge.py` covers the repair. This covers the part that makes
anyone reach for it: a leaf that lost its RNS identity tags a short burst of
announces with the slug it used to answer to, the gateway forwards that as
`extra.prev_node`, and until these tests it went no further than the JSON blob
in the telemetry row. Now /nodes/health says so, and stops saying so once the
two halves are one row again — from either direction.
"""

from trailcam.identity import prev_node_from_extra

OLD = "leaf-0a8bd4e9"  # what board 0016 announced before the reflash
NEW = "leaf-34f258f3"  # what it announced after
THIRD = "leaf-93573b76"  # and after the reflash after that


async def beat(client, token, node, site="north40", **over):
    r = await client.post(
        "/api/v1/telemetry",
        json={"site": site, "node": node, "kind": "camera", "battery_v": 3.3, **over},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r


async def health_by_slug(client, slug):
    r = await client.get("/api/v1/nodes/health")
    assert r.status_code == 200, r.text
    for n in r.json():
        if n["slug"] == slug:
            return n
    return None


async def camera_by_slug(client, slug):
    for c in (await client.get("/api/v1/cameras?include_hidden=true")).json():
        if c["slug"] == slug:
            return c
    return None


def test_prev_node_from_extra_takes_only_a_real_slug():
    assert prev_node_from_extra({"prev_node": OLD}) == OLD
    assert prev_node_from_extra({"prev_node": f"  {OLD} "}) == OLD
    # `extra` is whatever the gateway posted; none of this may raise.
    assert prev_node_from_extra(None) is None
    assert prev_node_from_extra({}) is None
    assert prev_node_from_extra({"prev_node": ""}) is None
    assert prev_node_from_extra({"prev_node": "   "}) is None
    assert prev_node_from_extra({"prev_node": 42}) is None
    assert prev_node_from_extra({"prev_node": {"slug": OLD}}) is None
    assert prev_node_from_extra("not a dict") is None


async def test_health_reports_the_slug_a_node_used_to_answer_to(client, device_token):
    """The whole point: a merge is owed, and you can see it without reading JSON."""
    await beat(client, device_token, OLD, battery_v=3.31)
    # ...board reflashed, identity regenerated, and it says so...
    await beat(client, device_token, NEW, extra={"prev_node": OLD, "hops": 1})

    row = await health_by_slug(client, NEW)
    assert row["prev_node"] == OLD
    # ...pointing at the row that still holds the other half of the history.
    old_cam = await camera_by_slug(client, OLD)
    assert row["prev_node_id"] == old_cam["id"]
    # The old row is not the one that changed identity; it says nothing.
    assert (await health_by_slug(client, OLD))["prev_node"] is None


async def test_it_outlives_the_announce_burst_that_reported_it(client, device_token):
    """The rule that matters: the newest beat CARRYING prev_node, not the newest beat.

    The leaf tags about a dozen announces after a re-identification and then
    never mentions it again. Reading `latest.extra` would show this for a few
    minutes and hide it for the weeks it actually needs to be visible."""
    await beat(client, device_token, OLD)
    await beat(client, device_token, NEW, extra={"prev_node": OLD})
    for _ in range(5):
        await beat(client, device_token, NEW, extra={"hops": 1})

    row = await health_by_slug(client, NEW)
    assert row["latest"]["extra"] == {"hops": 1}, "the burst is over — the precondition"
    assert row["prev_node"] == OLD


async def test_the_newest_report_wins_when_a_board_loses_its_identity_twice(
    client, device_token
):
    await beat(client, device_token, OLD)
    await beat(client, device_token, NEW, extra={"prev_node": OLD})
    await beat(client, device_token, THIRD, extra={"prev_node": OLD})
    await beat(client, device_token, THIRD, extra={"prev_node": NEW})

    assert (await health_by_slug(client, THIRD))["prev_node"] == NEW


async def test_merging_onto_the_original_row_clears_it(client, device_token):
    """The field repair: keep the old row, absorb the new one."""
    await beat(client, device_token, OLD)
    await beat(client, device_token, NEW, extra={"prev_node": OLD})
    keep = await camera_by_slug(client, OLD)

    r = await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": NEW})
    assert r.status_code == 200, r.text

    # One row left, and the beat that reported prev_node now sits on the row
    # whose own slug IS that prev_node. Nothing is owed.
    rows = (await client.get("/api/v1/nodes/health")).json()
    assert [n["slug"] for n in rows] == [OLD]
    assert rows[0]["prev_node"] is None
    assert rows[0]["prev_node_id"] is None


async def test_merging_onto_the_new_row_clears_it_too(client, device_token):
    """The other direction: keep the row the board actually announces under."""
    await beat(client, device_token, OLD)
    await beat(client, device_token, NEW, extra={"prev_node": OLD})
    keep = await camera_by_slug(client, NEW)

    r = await client.post(f"/api/v1/cameras/{keep['id']}/merge", json={"source": OLD})
    assert r.status_code == 200, r.text
    assert r.json()["camera"]["aliases"] == [OLD]

    rows = (await client.get("/api/v1/nodes/health")).json()
    assert [n["slug"] for n in rows] == [NEW]
    assert rows[0]["prev_node"] is None, "the retired slug is an alias here, not a slug"


async def test_it_stays_owed_while_the_split_stands(client, device_token):
    """A merge that was owed a month ago is still owed — no recency window."""
    await beat(client, device_token, OLD)
    await beat(client, device_token, NEW, extra={"prev_node": OLD})
    # An unrelated merge elsewhere must not clear this one.
    await beat(client, device_token, THIRD)
    third = await camera_by_slug(client, THIRD)
    await client.post(
        f"/api/v1/cameras/{third['id']}/merge", json={"source": OLD, "alias": False}
    )
    # OLD's row is gone (folded into THIRD) but the slug is not aliased anywhere,
    # so the history it names is unreachable — still worth saying the identity
    # changed, with no merge target to offer.
    row = await health_by_slug(client, NEW)
    assert row["prev_node"] == OLD
    assert row["prev_node_id"] is None


async def test_it_follows_a_chain_of_merges_to_the_row_that_holds_the_history(
    client, device_token
):
    await beat(client, device_token, OLD)
    await beat(client, device_token, THIRD)
    third = await camera_by_slug(client, THIRD)
    # OLD was already folded into THIRD, keeping OLD as an alias there.
    await client.post(f"/api/v1/cameras/{third['id']}/merge", json={"source": OLD})
    # Now the board comes back again and still names its oldest slug.
    await beat(client, device_token, NEW, extra={"prev_node": OLD})

    row = await health_by_slug(client, NEW)
    assert row["prev_node"] == OLD
    assert row["prev_node_id"] == third["id"], "resolved through the alias, as ingest does"


async def test_a_node_naming_its_own_slug_is_not_a_split(client, device_token):
    await beat(client, device_token, NEW, extra={"prev_node": NEW})
    assert (await health_by_slug(client, NEW))["prev_node"] is None


async def test_slugs_resolve_within_the_site(client, device_token):
    """Slugs are unique per site; a same-named node elsewhere is a different board."""
    await beat(client, device_token, OLD, site="south-ridge")
    await beat(client, device_token, NEW, site="north40", extra={"prev_node": OLD})

    row = await health_by_slug(client, NEW)
    assert row["prev_node"] == OLD
    assert row["prev_node_id"] is None, "the north40 half of this history does not exist"


async def test_a_quiet_mesh_reports_nothing(client, device_token):
    await beat(client, device_token, OLD)
    await beat(client, device_token, NEW, extra={"health": {"captures": 3}})
    for row in (await client.get("/api/v1/nodes/health")).json():
        assert row["prev_node"] is None
        assert row["prev_node_id"] is None
