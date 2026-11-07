"""Web UI routes, driven through httpx against the ASGI app with a real Farmer + FakeTwitch."""

import httpx
import pytest

from dropbot.farmer import Farmer
from dropbot.web.app import create_app

from .factories import NOW, campaign, drop, stream
from .fakes import FakeTwitch

TOKEN = "goodtoken0123456789abc"


@pytest.fixture
async def env(store):
    tw = FakeTwitch()
    farmer = Farmer(store, tw, clock=lambda: NOW)
    await farmer.restore_login()
    app = create_app(farmer)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot")
    yield farmer, tw, store, client
    await client.aclose()


async def test_healthz(env):
    _, _, _, c = env
    r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "logged_out"


async def test_pages_render_logged_out(env):
    _, _, _, c = env
    for path in ("/", "/campaigns", "/channels", "/login", "/partials/dashboard"):
        r = await c.get(path)
        assert r.status_code == 200, path
    assert "access_token" in (await c.get("/login")).text


async def test_login_flow_and_logout(env):
    farmer, _, store, c = env
    r = await c.post("/login", data={"pasted": "nope"})
    assert r.status_code == 303 and "error=" in r.headers["location"]
    r = await c.post("/login", data={"pasted": f"https://www.twitch.tv/#access_token={TOKEN}&x=y"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert farmer.state.status == "running"
    assert "Logged in as" in (await c.get("/login")).text
    await c.post("/logout")
    assert await store.get_account() is None


async def test_campaign_page_select_and_badges(env):
    farmer, tw, store, c = env
    await farmer.login(TOKEN)
    tw.campaign_list = [
        campaign("c1", drops=[drop("d1"), drop("sub", required=0)]),
        campaign("c2", linked=False),
    ]
    await farmer.tick()
    html = (await c.get("/campaigns")).text
    assert html.count('badge new">new') == 2
    assert "no account linked" in html and "not farmable by watching" in html
    assert 'badge new">new' not in (await c.get("/campaigns")).text  # shown once

    r = await c.post("/campaigns/c1/select", data={"selected": "1"})
    assert r.status_code == 303
    assert await store.selected_ids() == {"c1"}
    await c.post("/campaigns/c1/select", data={"selected": ""})
    assert await store.selected_ids() == frozenset()


async def test_points_channels_add_reorder_remove_import(env):
    farmer, tw, store, c = env
    await farmer.login(TOKEN)
    tw.add_user("1", "alpha")
    tw.add_user("2", "beta")
    await c.post("/channels/add", data={"login": "https://www.twitch.tv/Alpha"})
    await c.post("/channels/add", data={"login": "@beta"})
    r = await c.post("/channels/add", data={"login": "ghost"})
    assert "error=" in r.headers["location"]
    assert [p.login for p in await store.points_channels()] == ["alpha", "beta"]
    await c.post("/channels/2/move", data={"delta": "-1"})
    assert [p.login for p in await store.points_channels()] == ["beta", "alpha"]
    await c.post("/channels/2/remove")
    assert [p.login for p in await store.points_channels()] == ["alpha"]

    from dropbot.twitch import UserInfo

    tw.follows = [UserInfo("3", "gamma"), UserInfo("", "beta")]
    await c.post("/channels/import")
    assert [p.login for p in await store.points_channels()] == ["alpha", "gamma", "beta"]
    assert "gamma" in (await c.get("/channels")).text


async def test_dashboard_shows_watch_slots(env):
    farmer, tw, store, c = env
    await farmer.login(TOKEN)
    tw.campaign_list = [campaign("c1", drops=[drop("d1", required=60, current=15)])]
    tw.go_live(stream("1", viewers=1234))
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    html = (await c.get("/partials/dashboard")).text
    assert "user1" in html and "15/60 min" in html and "1,234" in html


async def test_protocol_error_banner_and_resume(env):
    farmer, tw, _, c = env
    await farmer.login(TOKEN)
    from dropbot.twitch import ProtocolError

    tw.fail_next = ProtocolError("Inventory", "PersistedQueryNotFound")
    await farmer.tick()
    assert "Twitch rejected a request" in (await c.get("/")).text
    await c.post("/resume")
    assert farmer.state.status == "running"


async def test_balance_check_and_refresh(env):
    farmer, tw, store, c = env
    await farmer.login(TOKEN)
    tw.add_user("7", "somebody")
    tw.balances["somebody"] = 4321
    html = (await c.get("/channels", params={"check": "https://www.twitch.tv/SomeBody/"})).text
    assert "4,321" in html and "add to list" in html
    assert "No Twitch channel named" in (await c.get("/channels", params={"check": "ghost"})).text

    await store.add_points_channel("7", "somebody")
    tw.balances["somebody"] = 5000
    r = await c.post("/channels/balances")
    assert r.status_code == 303 and "error" not in r.headers["location"]
    html = (await c.get("/channels")).text
    assert "5,000" in html and "Total across these channels: <strong>5,000" in html


async def test_watch_report_notice(env):
    farmer, _, _, c = env
    await farmer.login(TOKEN)
    farmer.state.watch_report_error = "Cannot connect to host spade.twitch.tv:443"
    assert "isn't blocking" in (await c.get("/")).text


async def test_watch_slot_shows_channel_points(env):
    farmer, tw, store, c = env
    await farmer.login(TOKEN)
    tw.campaign_list = [campaign("c1")]
    tw.go_live(stream("drop", viewers=999))
    tw.go_live(stream("p1", game=None, drops_enabled=False))
    tw.balances["userdrop"] = 777
    tw.balances["userp1"] = 12345
    await store.add_points_channel("p1", "userp1")
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    html = (await c.get("/partials/dashboard")).text
    assert "<strong>777</strong> Channel Points" in html
    assert "<strong>12,345</strong> Channel Points" in html
    # The Drop-only stream's balance is shown but not recorded as farmed.
    assert [e.channel_id for e in await store.recent_events(kinds=["points", "bonus"])] == []


async def test_campaigns_page_has_search_data(env):
    farmer, tw, _, c = env
    await farmer.login(TOKEN)
    tw.campaign_list = [campaign("c1", drops=[drop("d1")])]
    await farmer.tick()
    html = (await c.get("/campaigns")).text
    assert 'id="campaign-search"' in html
    assert 'data-search="some game"' in html
    assert 'data-search="campaign c1' in html and "drop d1" in html


async def test_dashboard_activity_lists_claims_not_points_ticks(env):
    farmer, _, store, c = env
    await farmer.login(TOKEN)
    await store.record("drop", campaign_id="c1", detail="Cool Hat")
    for _ in range(30):
        await store.record("points", amount=10, channel_id="1", channel_name="busy")
    html = (await c.get("/partials/dashboard")).text
    assert "Cool Hat" in html and "+10 on busy" not in html


@pytest.mark.parametrize(
    "headers,allowed",
    [
        ({}, True),  # curl / scripts
        ({"sec-fetch-site": "same-origin"}, True),
        ({"sec-fetch-site": "none"}, True),
        ({"sec-fetch-site": "cross-site"}, False),
        ({"sec-fetch-site": "same-site"}, False),
        ({"origin": "http://bot"}, True),
        ({"origin": "http://evil.example"}, False),
        ({"origin": "null"}, False),
    ],
)
async def test_cross_site_posts_are_rejected(env, headers, allowed):
    farmer, _, _, c = env
    await farmer.login(TOKEN)
    r = await c.post("/logout", headers=headers)
    assert (r.status_code != 403) is allowed
    assert (farmer.state.status == "logged_out") is allowed


async def test_get_requests_are_never_blocked(env):
    _, _, _, c = env
    assert (await c.get("/", headers={"sec-fetch-site": "cross-site"})).status_code == 200


async def test_htmx_select_swaps_just_the_card(env):
    farmer, tw, store, c = env
    await farmer.login(TOKEN)
    tw.campaign_list = [campaign("c1")]
    await farmer.tick()
    r = await c.post("/campaigns/c1/select", data={"selected": "1"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert (
        r.text.lstrip().startswith('<div class="panel campaign"')
        or 'class="panel campaign"' in r.text[:300]
    )
    assert "✓ Selected" in r.text and "<html" not in r.text
    assert await store.selected_ids() == {"c1"}


async def test_account_link_url_must_be_https(env):
    farmer, tw, _, c = env
    await farmer.login(TOKEN)
    from dataclasses import replace

    tw.campaign_list = [replace(campaign("c1", linked=False), link_url="javascript:alert(1)")]
    await farmer.tick()
    assert "javascript:" not in (await c.get("/campaigns")).text
