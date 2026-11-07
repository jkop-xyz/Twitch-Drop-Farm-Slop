"""End-to-end farming loop against FakeTwitch and a real (SQLite) Store."""

from datetime import timedelta

import pytest

from dropbot.farmer import Farmer
from dropbot.twitch import AuthError, ProtocolError, TransientError
from dropbot.twitch.api import DropSession

from .factories import NOW, campaign, drop, stream
from .fakes import FakeTwitch

TOKEN = "goodtoken0123456789abc"
REDIRECT = f"https://www.twitch.tv/#access_token={TOKEN}&scope=&token_type=bearer"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def tw():
    return FakeTwitch()


@pytest.fixture
async def farmer(store, tw):
    f = Farmer(store, tw, clock=Clock())
    await f.restore_login()
    return f


async def logged_in(farmer):
    await farmer.login(REDIRECT)
    return farmer


def watching(f):
    return [s.stream.channel_id for s in f.state.plan.slots]


# --- login -------------------------------------------------------------------------------


async def test_starts_logged_out(farmer):
    assert farmer.state.status == "logged_out"


async def test_login_by_pasting_redirect_url_persists(farmer, store, tw):
    await farmer.login(REDIRECT)
    assert farmer.state.status == "running"
    assert tw.authed == TOKEN
    acct = await store.get_account()
    assert (acct.user_id, acct.login, acct.device_id) == ("42", "me", "fakedevice")

    again = Farmer(store, tw, clock=Clock())
    await again.restore_login()
    assert again.state.status == "running"


async def test_login_with_garbage_is_rejected(farmer):
    with pytest.raises(AuthError):
        await farmer.login("https://www.twitch.tv/")
    assert farmer.state.status == "logged_out"


async def test_stored_token_revoked_goes_back_to_login(farmer, store, tw):
    await logged_in(farmer)
    tw.tokens.clear()
    again = Farmer(store, tw, clock=Clock())
    await again.restore_login()
    assert again.state.status == "logged_out"
    assert await store.get_account() is None


async def test_auth_error_during_tick_logs_out(farmer, store, tw):
    await logged_in(farmer)
    tw.fail_next = AuthError("401")
    await farmer.tick()
    assert farmer.state.status == "logged_out"
    assert await store.get_account() is None


async def test_logout(farmer, store):
    await logged_in(farmer)
    await farmer.logout()
    assert farmer.state.status == "logged_out" and await store.get_account() is None


# --- campaigns ---------------------------------------------------------------------------


async def test_new_campaigns_are_recorded_unselected_and_not_farmed(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1")]
    tw.go_live(stream("1"))
    await farmer.tick()
    states = await store.campaign_states()
    assert not states["c1"].selected and not states["c1"].seen
    assert farmer.state.plan.slots == ()


async def test_selected_campaign_is_farmed_on_biggest_stream(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1")]
    tw.go_live(stream("small", viewers=5))
    tw.go_live(stream("big", viewers=500))
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert watching(farmer) == ["big"]


async def test_allowed_channel_campaign_checks_those_channels(farmer, store, tw):
    await logged_in(farmer)
    c = campaign("c1", allowed={"9"})
    from dataclasses import replace

    tw.campaign_list = [replace(c, allowed_logins=("user9",))]
    tw.go_live(stream("9", viewers=1))
    tw.go_live(stream("other", viewers=999))
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert watching(farmer) == ["9"]


async def test_unlinked_selected_campaign_waits(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1", linked=False)]
    tw.go_live(stream("1"))
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert farmer.state.plan.slots == ()
    assert await store.selected_ids() == {"c1"}  # stays selected until linked


async def test_drop_is_claimed_when_progress_completes_and_campaign_deselected(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1", drops=[drop("d1", required=60, current=50)])]
    tw.go_live(stream("1"))
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert watching(farmer) == ["1"]

    tw.drop_sessions["1"] = DropSession("d1", 60)
    await farmer.tick()
    assert tw.claimed_drops == [("c1", "d1")]
    [event] = await store.recent_events(kinds=["drop"])
    assert event.campaign_id == "c1"
    assert await store.selected_ids() == frozenset()
    assert farmer.state.plan.slots == ()


async def test_deadline_order_moves_to_next_campaign_after_claim(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [
        campaign("soon", ends_in=timedelta(hours=3), allowed={"1"}, drops=[drop("a", current=59)]),
        campaign("late", ends_in=timedelta(days=3), allowed={"2"}),
    ]
    from dataclasses import replace

    tw.campaign_list = [
        replace(tw.campaign_list[0], allowed_logins=("user1",)),
        replace(tw.campaign_list[1], allowed_logins=("user2",)),
    ]
    tw.go_live(stream("1"))
    tw.go_live(stream("2"))
    await farmer.tick()
    await store.set_selected("soon", True)
    await store.set_selected("late", True)
    await farmer.tick()
    assert farmer.state.plan.drop_slot.reason.campaign_id == "soon"
    tw.drop_sessions["1"] = DropSession("a", 60)
    await farmer.tick()
    await farmer.tick()
    assert farmer.state.plan.drop_slot.reason.campaign_id == "late"


# --- points ------------------------------------------------------------------------------


async def test_points_channels_watched_bonus_claimed_and_gains_recorded(farmer, store, tw):
    await logged_in(farmer)
    for cid in ("1", "2", "3"):
        tw.go_live(stream(cid))
        tw.balances[f"user{cid}"] = 1000
        await store.add_points_channel(cid, f"user{cid}")
    tw.bonus["user1"] = "claim-x"
    await farmer.tick()
    assert watching(farmer) == ["1", "2"]
    assert tw.claimed_bonuses == [("1", "claim-x")]
    await farmer.tick()
    gains = await store.points_gained_since(NOW - timedelta(days=1))
    assert gains == {"1": 50}
    assert [e.kind for e in await store.recent_events(kinds=["bonus"])] == ["bonus"]


async def test_higher_ranked_points_channel_preempts_and_offline_frees_slot(farmer, store, tw):
    await logged_in(farmer)
    for cid in ("1", "2", "3"):
        await store.add_points_channel(cid, f"user{cid}")
    tw.go_live(stream("2"))
    tw.go_live(stream("3"))
    await farmer.tick()
    assert watching(farmer) == ["2", "3"]
    tw.go_live(stream("1"))
    await farmer.tick()
    assert watching(farmer) == ["1", "2"]
    tw.go_offline("1")
    await farmer.tick()
    assert watching(farmer) == ["2", "3"]


async def test_drop_takes_priority_over_points(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1")]
    tw.go_live(stream("drops", viewers=999))
    tw.go_live(stream("p1", game=None, drops_enabled=False))
    tw.go_live(stream("p2", game=None, drops_enabled=False))
    await store.add_points_channel("p1", "userp1")
    await store.add_points_channel("p2", "userp2")
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert watching(farmer) == ["drops", "p1"]


async def test_points_off_channel_is_detected_before_planning_and_only_watched_for_drops(
    farmer, store, tw
):
    await logged_in(farmer)
    for cid in ("1", "2", "3"):
        tw.go_live(stream(cid, game=None, drops_enabled=False))
        tw.balances[f"user{cid}"] = 100
        await store.add_points_channel(cid, f"user{cid}")
    tw.points_off.add("user1")
    await farmer.tick()
    assert watching(farmer) == ["2", "3"]  # never watched for points, not even on the first tick
    off = {p.channel_id: p.points_off for p in await store.points_channels()}
    assert off == {"1": True, "2": False, "3": False}

    # A Drop that can only be farmed there still gets it.
    tw.campaign_list = [campaign("c1", allowed={"1"})]
    tw.go_live(stream("1"))
    farmer.wake(refresh_campaigns=True)
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert watching(farmer) == ["1", "2"]
    assert farmer.state.slot_points_off == {"1"}


async def test_channel_turning_points_off_is_dropped_and_turning_on_again_is_picked_up(
    farmer, store, tw
):
    await logged_in(farmer)
    for cid in ("1", "2", "3"):
        tw.go_live(stream(cid, game=None, drops_enabled=False))
        tw.balances[f"user{cid}"] = 100
        await store.add_points_channel(cid, f"user{cid}")
    await farmer.tick()
    assert watching(farmer) == ["1", "2"]
    tw.points_off.add("user1")
    await farmer.tick()  # noticed while watching
    await farmer.tick()
    assert watching(farmer) == ["2", "3"]
    tw.points_off.clear()
    await farmer.refresh_balances()
    await farmer.tick()
    assert watching(farmer) == ["1", "2"]


async def test_unknown_channel_is_checked_only_once_per_session(farmer, store, tw):
    await logged_in(farmer)
    await store.add_points_channel("9", "gone")  # Twitch returns nothing for it
    await farmer.tick()
    tw.calls.clear()
    await farmer.tick()
    assert "points_many" not in tw.calls


# --- failures ----------------------------------------------------------------------------


async def test_protocol_error_pauses_until_resumed(farmer, tw):
    await logged_in(farmer)
    tw.fail_next = ProtocolError("Inventory", "PersistedQueryNotFound")
    await farmer.tick()
    assert farmer.state.status == "paused"
    assert "Inventory" in farmer.state.protocol_error
    assert farmer.state.plan.slots == ()
    farmer.resume()
    assert farmer.state.status == "running" and farmer.state.protocol_error is None


async def test_transient_error_keeps_running(farmer, tw):
    await logged_in(farmer)
    tw.fail_next = TransientError("network down")
    await farmer.tick()
    assert farmer.state.status == "running"
    assert farmer.state.transient_error == "network down"
    await farmer.tick()
    assert farmer.state.transient_error is None


async def test_campaigns_are_cached_between_ticks(farmer, tw):
    await logged_in(farmer)
    await farmer.tick()
    await farmer.tick()
    assert tw.calls.count("campaigns") == 1
    farmer.clock.now += timedelta(minutes=16)
    await farmer.tick()
    assert tw.calls.count("campaigns") == 2


# --- balances ----------------------------------------------------------------------------


async def test_refresh_balances_updates_all_channels_without_recording_gains(farmer, store, tw):
    await logged_in(farmer)
    for cid, bal in (("1", 100), ("2", 200)):
        await store.add_points_channel(cid, f"user{cid}")
        tw.balances[f"user{cid}"] = bal
    assert await farmer.refresh_balances() == 2
    tw.balances["user1"] = 900  # e.g. the owner watched manually
    await farmer.refresh_balances()
    info = {i.channel.channel_id: i.balance for i in await store.points_channel_info()}
    assert info == {"1": 900, "2": 200}
    assert await store.recent_events(kinds=["points"]) == []


async def test_unwatched_balances_refresh_every_30_minutes(farmer, store, tw):
    await logged_in(farmer)
    await store.add_points_channel("1", "user1")  # offline
    tw.balances["user1"] = 100
    await farmer.tick()
    assert (await store.points_channel_info())[0].balance == 100
    tw.balances["user1"] = 150
    await farmer.tick()
    assert (await store.points_channel_info())[0].balance == 100
    farmer.clock.now += timedelta(minutes=31)
    await farmer.tick()
    assert (await store.points_channel_info())[0].balance == 150


async def test_check_balance_on_any_channel(farmer, tw):
    await logged_in(farmer)
    tw.add_user("7", "somebody")
    tw.balances["somebody"] = 4321
    user, pts = await farmer.check_balance("somebody")
    assert user.channel_id == "7" and pts.balance == 4321
    assert await farmer.check_balance("ghost") is None


async def test_failing_minute_watched_reports_get_their_own_notice(farmer, tw):
    await logged_in(farmer)
    s = stream("1")
    tw.spade_error = TransientError("Cannot connect to host spade.twitch.tv:443")
    assert not await farmer._send_minute_watched(s)
    assert "spade" in farmer.state.watch_report_error
    assert farmer.state.transient_error is None
    tw.spade_error = None
    assert await farmer._send_minute_watched(s)
    assert farmer.state.watch_report_error is None


# --- robustness (code review fixes) ------------------------------------------------------


async def test_unexpected_error_does_not_stop_the_loop(farmer, tw):
    await logged_in(farmer)
    tw.fail_next = RuntimeError("database went away")
    await farmer.tick()  # must not raise
    assert farmer.state.status == "running"
    assert "database went away" in farmer.state.transient_error
    await farmer.tick()
    assert farmer.state.transient_error is None


async def test_stream_that_keeps_failing_to_watch_is_skipped_for_a_while(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1")]
    tw.go_live(stream("big", viewers=999))
    tw.go_live(stream("small", viewers=5))
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert watching(farmer) == ["big"]

    big = farmer.state.plan.slots[0].stream
    farmer._record_watch(big, False)
    farmer._record_watch(big, False)
    assert "big" not in farmer.state.skipped
    farmer._record_watch(big, False)
    assert "big" in farmer.state.skipped
    await farmer.tick()
    assert watching(farmer) == ["small"]
    assert "big" in farmer.state.live  # still shown as live

    farmer.clock.now += timedelta(minutes=11)
    await farmer.tick()
    assert watching(farmer) == ["small"]  # current stream is kept while eligible
    assert farmer.state.skipped == {}


async def test_successful_watch_resets_failure_count(farmer, tw):
    await logged_in(farmer)
    s = stream("1")
    farmer._record_watch(s, False)
    farmer._record_watch(s, False)
    farmer._record_watch(s, True)
    farmer._record_watch(s, False)
    assert farmer.state.skipped == {}


async def test_allow_listed_campaign_also_found_through_game_directory(farmer, store, tw):
    """Allow lists longer than we look up: a live allowed channel beyond the lookup cap."""
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1", allowed={"far"})]  # no logins to look up
    tw.go_live(stream("far", viewers=3))
    tw.go_live(stream("notallowed", viewers=999))
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert watching(farmer) == ["far"]


async def test_balance_refresh_is_batched(farmer, store, tw):
    await logged_in(farmer)
    for cid in ("1", "2", "3"):
        await store.add_points_channel(cid, f"user{cid}")
        tw.balances[f"user{cid}"] = 10
    tw.calls.clear()
    await farmer.refresh_balances()
    assert tw.calls.count("points_many") == 1


# --- per-account data --------------------------------------------------------------------


async def test_logout_hides_account_data_and_relogin_restores_it(farmer, store, tw):
    await logged_in(farmer)
    tw.campaign_list = [campaign("c1")]
    tw.go_live(stream("1"))
    await store.add_points_channel("1", "user1")
    await farmer.tick()
    await store.set_selected("c1", True)
    await farmer.tick()
    assert farmer.state.plan.slots and farmer.state.campaigns

    await farmer.logout()
    assert farmer.state.plan.slots == () and farmer.state.campaigns == []
    assert farmer.state.live == {} and farmer.state.slot_balances == {}
    assert await store.points_channels() == [] and await store.selected_ids() == frozenset()

    await farmer.login(TOKEN)
    assert [p.login for p in await store.points_channels()] == ["user1"]
    assert await store.selected_ids() == {"c1"}


async def test_other_account_starts_empty(farmer, store, tw):
    from dropbot.twitch import TokenInfo
    from dropbot.twitch.protocol import CLIENT_ID

    await logged_in(farmer)
    await store.add_points_channel("1", "user1")
    await farmer.logout()
    tw.tokens["othertoken0123456789abc"] = TokenInfo("99", "someoneelse", CLIENT_ID)
    await farmer.login("othertoken0123456789abc")
    assert farmer.state.account.login == "someoneelse"
    assert await store.points_channels() == []


async def test_first_login_adopts_data_left_unowned_by_the_upgrade(farmer, store, tw):
    """Upgraded while logged out: migration 0002 left rows with owner ''."""
    store.use_account("")
    await store.add_points_channel("1", "legacychannel")
    await store.record("drop", campaign_id="c1", detail="Old Hat")
    store.use_account(None)
    await farmer.login(TOKEN)
    assert [p.login for p in await store.points_channels()] == ["legacychannel"]
    assert await store.count_events("drop") == 1
