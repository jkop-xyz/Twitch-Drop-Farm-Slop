"""Domain objects. Terms follow GLOSSARY.md.

These are plain immutable values with no I/O, so the Scheduler can be tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Twitch lets a finished Drop be claimed for up to 24h after its campaign ends.
CLAIM_GRACE = timedelta(hours=24)

# Games whose campaigns can be earned on any channel, regardless of the game being streamed
# (per TwitchDropsMiner).
SPECIAL_GAME_IDS = frozenset({"509663", "509672"})


@dataclass(frozen=True)
class Game:
    id: str
    name: str
    slug: str = ""

    @property
    def is_special(self) -> bool:
        return self.id in SPECIAL_GAME_IDS


@dataclass(frozen=True)
class Drop:
    id: str
    name: str
    required_minutes: int
    current_minutes: int = 0
    claimed: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    precondition_ids: tuple[str, ...] = ()
    rewards: tuple[str, ...] = ()
    image_url: str = ""

    @property
    def farmable_by_watching(self) -> bool:
        """False for Drops that need a subscription or another action instead of minutes."""
        return self.required_minutes > 0

    @property
    def minutes_done(self) -> bool:
        return self.current_minutes >= self.required_minutes

    def in_window(self, now: datetime) -> bool:
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.ends_at is not None and now >= self.ends_at:
            return False
        return True


@dataclass(frozen=True)
class Campaign:
    """A Drop Campaign."""

    id: str
    name: str
    game: Game
    starts_at: datetime
    ends_at: datetime
    drops: tuple[Drop, ...]
    requires_account_link: bool = True
    account_linked: bool = False
    link_url: str = ""
    # None means "any channel streaming the game with Drops enabled".
    allowed_channel_ids: frozenset[str] | None = None
    # Logins for allowed_channel_ids, used to look up whether those channels are live.
    allowed_logins: tuple[str, ...] = ()
    image_url: str = ""

    def running(self, now: datetime) -> bool:
        return self.starts_at <= now < self.ends_at

    def ended(self, now: datetime) -> bool:
        return now >= self.ends_at

    @property
    def link_ok(self) -> bool:
        return self.account_linked or not self.requires_account_link

    def _preconditions_met(self, drop: Drop) -> bool:
        claimed = {d.id for d in self.drops if d.claimed}
        return all(pid in claimed for pid in drop.precondition_ids)

    def earnable_drops(self, now: datetime) -> list[Drop]:
        """Drops that watching the campaign right now would make progress on."""
        return [
            d
            for d in self.drops
            if not d.claimed
            and d.farmable_by_watching
            and not d.minutes_done
            and d.in_window(now)
            and self._preconditions_met(d)
        ]

    def claimable_drops(self, now: datetime) -> list[Drop]:
        if now >= self.ends_at + CLAIM_GRACE or not self.link_ok:
            return []
        return [
            d for d in self.drops if not d.claimed and d.farmable_by_watching and d.minutes_done
        ]

    def is_farmable(self, now: datetime) -> bool:
        """True if this is a Farmable Campaign (assuming it is also selected)."""
        return self.running(now) and self.link_ok and bool(self.earnable_drops(now))

    def is_done(self) -> bool:
        """Every Drop is claimed or can't be earned by watching."""
        return all(d.claimed or not d.farmable_by_watching for d in self.drops)

    def accepts(self, stream: Stream) -> bool:
        """Whether watching this stream counts toward this campaign."""
        if (
            self.allowed_channel_ids is not None
            and stream.channel_id not in self.allowed_channel_ids
        ):
            return False
        if self.game.is_special:
            return True
        return stream.game_id == self.game.id and stream.drops_enabled


@dataclass(frozen=True)
class Stream:
    """A live broadcast on a channel."""

    channel_id: str
    login: str
    display_name: str = ""
    broadcast_id: str = ""
    game_id: str | None = None
    game_name: str = ""
    viewers: int = 0
    drops_enabled: bool = False
    title: str = ""

    @property
    def name(self) -> str:
        return self.display_name or self.login


@dataclass(frozen=True)
class PointsChannel:
    channel_id: str
    login: str
    display_name: str = ""
    rank: int = 0
    # False: the channel has Channel Points turned off, so it's only worth watching for a Drop.
    # None: not checked yet (treated as on).
    points_enabled: bool | None = None

    @property
    def name(self) -> str:
        return self.display_name or self.login

    @property
    def points_off(self) -> bool:
        return self.points_enabled is False


@dataclass(frozen=True)
class DropReason:
    campaign_id: str
    also_points: bool = False


@dataclass(frozen=True)
class PointsReason:
    pass


@dataclass(frozen=True)
class SlotAssignment:
    stream: Stream
    reason: DropReason | PointsReason


@dataclass(frozen=True)
class Plan:
    slots: tuple[SlotAssignment, ...] = ()
    # (campaign_id, drop) pairs ready to be claimed
    claims: tuple[tuple[str, Drop], ...] = ()
    # Selected Campaigns that should leave the selection
    deselect: frozenset[str] = field(default_factory=frozenset)

    @property
    def drop_slot(self) -> SlotAssignment | None:
        for s in self.slots:
            if isinstance(s.reason, DropReason):
                return s
        return None
