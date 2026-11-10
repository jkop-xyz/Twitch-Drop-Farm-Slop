"""Pure decision logic: what each Watch Slot should watch, what to claim, what to deselect.

The Scheduler does no I/O. The farming loop feeds it a snapshot and applies the Plan it returns.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .domain import (
    Campaign,
    DropReason,
    Plan,
    PointsChannel,
    PointsReason,
    SlotAssignment,
    Stream,
)

# The number of streams Twitch credits at the same time for one Account.
WATCH_SLOTS = 2


@dataclass(frozen=True)
class Snapshot:
    now: datetime
    campaigns: Sequence[Campaign]
    selected: frozenset[str]
    # Every known live stream, keyed by channel id: Points Channels and Drop candidates alike.
    live: Mapping[str, Stream]
    points_channels: Sequence[PointsChannel]
    # Channel currently watched for a Drop, used to avoid needless switching.
    current_drop_channel: str | None = None
    slots: int = WATCH_SLOTS


def farmable_campaigns(snap: Snapshot) -> list[Campaign]:
    """Farmable Campaigns, soonest-ending first."""
    found = [c for c in snap.campaigns if c.id in snap.selected and c.is_farmable(snap.now)]
    return sorted(found, key=lambda c: (c.ends_at, c.id))


def _points_rank(points_channels: Iterable[PointsChannel]) -> dict[str, int]:
    """Rank of each Points Channel that earns Channel Points (turned-off ones are left out)."""
    ordered = sorted((p for p in points_channels if not p.points_off), key=lambda p: p.rank)
    return {p.channel_id: i for i, p in enumerate(ordered)}


def pick_drop_stream(
    campaign: Campaign,
    live: Mapping[str, Stream],
    points_rank: Mapping[str, int],
    current: str | None,
) -> Stream | None:
    """Choose the stream to watch for a campaign.

    Order: a Points Channel (highest-ranked), otherwise the eligible stream with most viewers.
    The current stream is kept while it stays in the best available class, to avoid flapping.
    """
    eligible = [s for s in live.values() if campaign.accepts(s)]
    if not eligible:
        return None
    points = [s for s in eligible if s.channel_id in points_rank]
    current_stream = live.get(current) if current else None
    if points:
        if current_stream is not None and current_stream in points:
            return current_stream
        return min(points, key=lambda s: points_rank[s.channel_id])
    if current_stream is not None and current_stream in eligible:
        return current_stream
    return max(eligible, key=lambda s: (s.viewers, s.channel_id))


def competes(stream: Stream, farming: Campaign) -> bool:
    """Would watching `stream` next to the Drop stream take progress away from `farming`?

    Twitch credits Drop progress to one channel at a time. Watching a second stream with Drops
    of its own (another game's campaign, say) can move the progress there, and the selected Drop
    stalls. A stream that counts for the same campaign is harmless.
    """
    return stream.drops_enabled and not farming.accepts(stream)


def plan(snap: Snapshot) -> Plan:
    points_rank = _points_rank(snap.points_channels)
    slots: list[SlotAssignment] = []
    farming: Campaign | None = None

    for campaign in farmable_campaigns(snap):
        stream = pick_drop_stream(campaign, snap.live, points_rank, snap.current_drop_channel)
        if stream is not None:
            reason = DropReason(campaign.id, also_points=stream.channel_id in points_rank)
            slots.append(SlotAssignment(stream, reason))
            farming = campaign
            break

    taken = {s.stream.channel_id for s in slots}
    # A channel with Channel Points turned off earns nothing here: it's only watched for a Drop.
    for pc in sorted(snap.points_channels, key=lambda p: p.rank):
        if len(slots) >= snap.slots:
            break
        if pc.points_off:
            continue
        stream = snap.live.get(pc.channel_id)
        if stream is not None and farming is not None and competes(stream, farming):
            continue
        if stream is not None and pc.channel_id not in taken:
            slots.append(SlotAssignment(stream, PointsReason()))
            taken.add(pc.channel_id)

    claims = tuple(
        (c.id, d)
        for c in snap.campaigns
        if c.id in snap.selected
        for d in c.claimable_drops(snap.now)
    )

    deselect = frozenset(
        c.id
        for c in snap.campaigns
        if c.id in snap.selected
        and (c.is_done() or (c.ended(snap.now) and not c.claimable_drops(snap.now)))
    )

    return Plan(slots=tuple(slots[: snap.slots]), claims=claims, deselect=deselect)
