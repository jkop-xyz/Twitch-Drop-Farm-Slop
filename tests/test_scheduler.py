from datetime import timedelta

from dropbot.domain import DropReason, PointsReason
from dropbot.scheduler import Snapshot, plan

from .factories import NOW, OTHER_GAME, campaign, drop, live, points, stream


def snap(campaigns=(), selected=None, streams=(), pts=(), current=None, slots=2, off=()):
    sel = frozenset(c.id for c in campaigns) if selected is None else frozenset(selected)
    return Snapshot(
        now=NOW,
        campaigns=list(campaigns),
        selected=sel,
        live=live(*streams),
        points_channels=points(*pts, off=off),
        current_drop_channel=current,
        slots=slots,
    )


def watched(p):
    return [s.stream.channel_id for s in p.slots]


# --- deadline ordering -------------------------------------------------------------------


def test_soonest_ending_farmable_campaign_gets_the_drop_slot():
    late = campaign("late", ends_in=timedelta(days=5), allowed={"1"})
    soon = campaign("soon", ends_in=timedelta(hours=5), allowed={"2"})
    p = plan(snap([late, soon], streams=[stream("1"), stream("2")]))
    assert p.drop_slot.reason == DropReason("soon")
    assert p.drop_slot.stream.channel_id == "2"


def test_falls_through_to_next_campaign_when_soonest_has_no_live_stream():
    soon = campaign("soon", ends_in=timedelta(hours=5), allowed={"offline"})
    late = campaign("late", ends_in=timedelta(days=5), allowed={"1"})
    p = plan(snap([soon, late], streams=[stream("1")]))
    assert p.drop_slot.reason.campaign_id == "late"


def test_unselected_campaign_is_never_farmed():
    c = campaign("c1")
    p = plan(snap([c], selected=[], streams=[stream("1")]))
    assert p.slots == ()


# --- account link ------------------------------------------------------------------------


def test_selected_campaign_without_account_link_gets_no_slot():
    c = campaign("c1", requires_link=True, linked=False)
    p = plan(snap([c], streams=[stream("1")]))
    assert p.drop_slot is None


def test_campaign_not_requiring_link_is_farmable_when_unlinked():
    c = campaign("c1", requires_link=False, linked=False)
    p = plan(snap([c], streams=[stream("1")]))
    assert p.drop_slot.reason.campaign_id == "c1"


def test_unlinked_campaign_drops_are_not_claimed():
    c = campaign("c1", linked=False, drops=[drop("d1", required=60, current=60)])
    assert plan(snap([c])).claims == ()


# --- not farmable by watching --------------------------------------------------------------


def test_campaign_with_only_non_watch_drops_left_is_done_and_deselected():
    c = campaign("c1", drops=[drop("a", claimed=True), drop("sub", required=0)])
    p = plan(snap([c], streams=[stream("1")]))
    assert p.drop_slot is None
    assert p.deselect == {"c1"}


def test_drop_waiting_on_unclaimed_precondition_is_not_earnable():
    c = campaign("c1", drops=[drop("a", required=0), drop("b", precondition_ids=("a",))])
    assert plan(snap([c], streams=[stream("1")])).drop_slot is None


# --- stream choice for a Drop ------------------------------------------------------------


def test_drop_prefers_a_points_channel_over_a_bigger_stream():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("big", viewers=9000), stream("pc", viewers=5)], pts=["pc"]))
    assert p.drop_slot.stream.channel_id == "pc"
    assert p.drop_slot.reason.also_points is True


def test_drop_prefers_highest_ranked_points_channel():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("low"), stream("high")], pts=["high", "low"]))
    assert p.drop_slot.stream.channel_id == "high"


def test_drop_falls_back_to_most_viewers():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("a", viewers=10), stream("b", viewers=500)]))
    assert p.drop_slot.stream.channel_id == "b"
    assert p.drop_slot.reason.also_points is False


def test_drop_ignores_streams_without_drops_enabled_or_wrong_game():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("a", drops_enabled=False), stream("b", game=OTHER_GAME)]))
    assert p.drop_slot is None


def test_drop_respects_allowed_channel_list():
    c = campaign("c1", allowed={"ok"})
    p = plan(snap([c], streams=[stream("ok", viewers=1), stream("other", viewers=999)]))
    assert p.drop_slot.stream.channel_id == "ok"


def test_current_drop_stream_is_kept_while_still_eligible():
    c = campaign("c1")
    p = plan(
        snap([c], streams=[stream("cur", viewers=10), stream("big", viewers=999)], current="cur")
    )
    assert p.drop_slot.stream.channel_id == "cur"


def test_points_channel_going_live_takes_drop_slot_from_non_points_stream():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("cur"), stream("pc")], pts=["pc"], current="cur"))
    assert p.drop_slot.stream.channel_id == "pc"


# --- drops before points -----------------------------------------------------------------


def test_drop_slot_comes_before_points_channels():
    c = campaign("c1", allowed={"d"})
    p = plan(snap([c], streams=[stream("d"), stream("p1"), stream("p2")], pts=["p1", "p2"]))
    assert watched(p) == ["d", "p1"]
    assert isinstance(p.slots[1].reason, PointsReason)


def test_shared_drop_and_points_stream_frees_a_slot_for_next_points_channel():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("p1"), stream("p2")], pts=["p1", "p2"]))
    assert watched(p) == ["p1", "p2"]
    assert p.slots[0].reason == DropReason("c1", also_points=True)


def test_without_farmable_campaign_both_slots_go_to_points():
    p = plan(snap([], streams=[stream("p1"), stream("p2"), stream("p3")], pts=["p1", "p2", "p3"]))
    assert watched(p) == ["p1", "p2"]


def test_points_channel_with_points_off_gets_no_points_slot():
    p = plan(
        snap(
            [],
            streams=[stream("p1"), stream("p2"), stream("p3")],
            pts=["p1", "p2", "p3"],
            off=["p1"],
        )
    )
    assert watched(p) == ["p2", "p3"]


def test_points_off_channel_is_still_watched_for_a_drop_but_not_preferred():
    c = campaign("c1")
    both = [stream("off", viewers=9000), stream("on", viewers=5)]
    p = plan(snap([c], streams=both, pts=["off", "on"], off=["off"]))
    assert p.drop_slot.stream.channel_id == "on"  # the one that also earns points
    only = plan(snap([c], streams=[stream("off")], pts=["off"], off=["off"], slots=1))
    assert watched(only) == ["off"]
    assert only.drop_slot.reason.also_points is False


# --- points preemption & switching -------------------------------------------------------


def test_higher_ranked_points_channel_going_live_takes_a_slot():
    before = plan(snap([], streams=[stream("p2"), stream("p3")], pts=["p1", "p2", "p3"]))
    after = plan(
        snap([], streams=[stream("p1"), stream("p2"), stream("p3")], pts=["p1", "p2", "p3"])
    )
    assert watched(before) == ["p2", "p3"]
    assert watched(after) == ["p1", "p2"]


def test_switches_when_watched_drop_stream_goes_offline():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("other")], current="gone"))
    assert p.drop_slot.stream.channel_id == "other"


def test_switches_when_watched_drop_stream_changes_game():
    c = campaign("c1")
    p = plan(snap([c], streams=[stream("cur", game=OTHER_GAME), stream("other")], current="cur"))
    assert p.drop_slot.stream.channel_id == "other"


# --- idle --------------------------------------------------------------------------------


def test_idle_when_nothing_is_farmable_or_live():
    c = campaign("c1")
    assert plan(snap([c], streams=[], pts=["p1"])).slots == ()


# --- claims and deselection --------------------------------------------------------------


def test_finished_drop_is_claimed():
    c = campaign("c1", drops=[drop("d1", required=60, current=60), drop("d2")])
    p = plan(snap([c], streams=[stream("1")]))
    assert [d.id for _, d in p.claims] == ["d1"]


def test_drop_can_be_claimed_within_24h_after_campaign_end():
    c = campaign("c1", ends_in=timedelta(hours=-2), drops=[drop("d1", current=60)])
    p = plan(snap([c]))
    assert [d.id for _, d in p.claims] == ["d1"]
    assert "c1" not in p.deselect


def test_ended_campaign_with_nothing_to_claim_is_deselected():
    c = campaign("c1", ends_in=timedelta(hours=-1))
    assert plan(snap([c])).deselect == {"c1"}


def test_fully_claimed_campaign_is_deselected():
    c = campaign("c1", drops=[drop("d1", claimed=True)])
    assert plan(snap([c])).deselect == {"c1"}


def test_upcoming_campaign_is_not_farmed_yet():
    c = campaign("c1", started_ago=timedelta(hours=-3))
    assert plan(snap([c], streams=[stream("1")])).drop_slot is None
