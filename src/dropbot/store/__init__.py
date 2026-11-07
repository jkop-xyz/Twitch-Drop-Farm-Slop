"""Persistence. Postgres primary, SQLite fallback (docs/adr/0002)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ..domain import Campaign, PointsChannel
from .models import AccountRow, CampaignStateRow, EventRow, PointsChannelRow

DEFAULT_SQLITE_URL = "sqlite+aiosqlite:////data/bot.db"


def normalize_url(url: str | None) -> str:
    """Map DATABASE_URL values onto async drivers. Unset means the SQLite fallback."""
    if not url:
        return DEFAULT_SQLITE_URL
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    if url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + url[len("sqlite:///") :]
    return url


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; everything we store is UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class NoAccount(RuntimeError):
    """Per-account data was written while no account is logged in."""


@dataclass(frozen=True)
class Account:
    access_token: str
    user_id: str
    login: str
    display_name: str = ""
    device_id: str = ""

    @property
    def name(self) -> str:
        return self.display_name or self.login


@dataclass(frozen=True)
class CampaignState:
    campaign_id: str
    seen: bool
    selected: bool
    first_seen_at: datetime


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str  # "drop" | "bonus" | "points"
    channel_id: str | None
    channel_name: str
    campaign_id: str | None
    amount: int
    detail: str


@dataclass(frozen=True)
class PointsChannelInfo:
    channel: PointsChannel
    balance: int | None
    balance_at: datetime | None


class Store:
    def __init__(self, url: str | None) -> None:
        self.url = normalize_url(url)
        self.engine: AsyncEngine = create_async_engine(self.url)
        self._session = async_sessionmaker(self.engine, expire_on_commit=False)
        # Twitch user id whose data reads and writes apply to. None while logged out:
        # reads then return nothing and writes raise NoAccount.
        self._user: str | None = None

    def use_account(self, user_id: str | None) -> None:
        """Scope Points Channels, campaign selections and history to one Twitch account."""
        self._user = user_id

    async def adopt_unowned(self, user_id: str) -> None:
        """Give data from before per-account storage (owner '') to this account.

        Migration 0002 assigns existing rows to the stored account; if nobody was logged in
        at upgrade time they are left with an empty owner, and the next login claims them.
        """
        async with self._session.begin() as s:
            for model in (CampaignStateRow, PointsChannelRow, EventRow):
                await s.execute(update(model).where(model.user_id == "").values(user_id=user_id))

    @property
    def account_id(self) -> str | None:
        return self._user

    def _uid(self) -> str:
        if self._user is None:
            raise NoAccount("not logged in")
        return self._user

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    async def migrate(self) -> None:
        cfg = Config()
        cfg.set_main_option("script_location", "dropbot:migrations")

        def run(sync_conn):
            cfg.attributes["connection"] = sync_conn
            command.upgrade(cfg, "head")

        async with self.engine.begin() as conn:
            await conn.run_sync(run)

    async def close(self) -> None:
        await self.engine.dispose()

    # --- Account ---------------------------------------------------------------------------

    async def get_account(self) -> Account | None:
        async with self._session() as s:
            row = await s.get(AccountRow, 1)
            if row is None:
                return None
            return Account(
                row.access_token, row.user_id, row.login, row.display_name, row.device_id
            )

    async def save_account(self, account: Account) -> None:
        async with self._session.begin() as s:
            row = await s.get(AccountRow, 1)
            if row is None:
                row = AccountRow(id=1)
                s.add(row)
            row.access_token = account.access_token
            row.user_id = account.user_id
            row.login = account.login
            row.display_name = account.display_name
            row.device_id = account.device_id
            row.updated_at = datetime.now(UTC)

    async def clear_account(self) -> None:
        async with self._session.begin() as s:
            await s.execute(delete(AccountRow))

    # --- Campaign selection ----------------------------------------------------------------

    async def observe_campaigns(self, campaigns: Iterable[Campaign]) -> None:
        """Record campaigns not seen before as unselected and new."""
        now = datetime.now(UTC)
        uid = self._uid()
        async with self._session.begin() as s:
            known = set(
                (
                    await s.scalars(
                        select(CampaignStateRow.campaign_id).where(CampaignStateRow.user_id == uid)
                    )
                ).all()
            )
            for c in campaigns:
                if c.id in known:
                    continue
                s.add(
                    CampaignStateRow(
                        user_id=uid,
                        campaign_id=c.id,
                        name=c.name,
                        game_name=c.game.name,
                        ends_at=c.ends_at,
                        first_seen_at=now,
                        seen=False,
                        selected=False,
                    )
                )
                known.add(c.id)

    async def campaign_states(self) -> dict[str, CampaignState]:
        if self._user is None:
            return {}
        async with self._session() as s:
            rows = (
                await s.scalars(
                    select(CampaignStateRow).where(CampaignStateRow.user_id == self._user)
                )
            ).all()
            return {
                r.campaign_id: CampaignState(
                    r.campaign_id, r.seen, r.selected, _utc(r.first_seen_at)
                )
                for r in rows
            }

    async def selected_ids(self) -> frozenset[str]:
        if self._user is None:
            return frozenset()
        async with self._session() as s:
            ids = await s.scalars(
                select(CampaignStateRow.campaign_id).where(
                    CampaignStateRow.user_id == self._user, CampaignStateRow.selected.is_(True)
                )
            )
            return frozenset(ids.all())

    async def set_selected(self, campaign_id: str, selected: bool) -> None:
        async with self._session.begin() as s:
            await s.execute(
                update(CampaignStateRow)
                .where(
                    CampaignStateRow.user_id == self._uid(),
                    CampaignStateRow.campaign_id == campaign_id,
                )
                .values(selected=selected, seen=True)
            )

    async def deselect(self, campaign_ids: Iterable[str]) -> None:
        ids = list(campaign_ids)
        if not ids:
            return
        async with self._session.begin() as s:
            await s.execute(
                update(CampaignStateRow)
                .where(
                    CampaignStateRow.user_id == self._uid(), CampaignStateRow.campaign_id.in_(ids)
                )
                .values(selected=False)
            )

    async def mark_all_seen(self) -> None:
        if self._user is None:
            return
        async with self._session.begin() as s:
            await s.execute(
                update(CampaignStateRow)
                .where(CampaignStateRow.user_id == self._uid())
                .values(seen=True)
            )

    # --- Points Channels -------------------------------------------------------------------

    async def points_channels(self) -> list[PointsChannel]:
        return [i.channel for i in await self.points_channel_info()]

    def _channels(self):
        return (
            select(PointsChannelRow)
            .where(PointsChannelRow.user_id == self._uid())
            .order_by(PointsChannelRow.rank)
        )

    async def points_channel_info(self) -> list[PointsChannelInfo]:
        if self._user is None:
            return []
        async with self._session() as s:
            rows = (await s.scalars(self._channels())).all()
            return [
                PointsChannelInfo(
                    PointsChannel(r.channel_id, r.login, r.display_name, r.rank),
                    r.balance,
                    _utc(r.balance_at),
                )
                for r in rows
            ]

    async def add_points_channel(self, channel_id: str, login: str, display_name: str = "") -> bool:
        """Append a channel at the bottom of the list. Returns False if it's already there."""
        uid = self._uid()
        async with self._session.begin() as s:
            if await s.get(PointsChannelRow, (uid, channel_id)) is not None:
                return False
            top = await s.scalar(
                select(func.max(PointsChannelRow.rank)).where(PointsChannelRow.user_id == uid)
            )
            s.add(
                PointsChannelRow(
                    user_id=uid,
                    channel_id=channel_id,
                    login=login,
                    display_name=display_name,
                    rank=(top if top is not None else -1) + 1,
                )
            )
            return True

    async def remove_points_channel(self, channel_id: str) -> None:
        async with self._session.begin() as s:
            await s.execute(
                delete(PointsChannelRow).where(
                    PointsChannelRow.user_id == self._uid(),
                    PointsChannelRow.channel_id == channel_id,
                )
            )

    async def reorder_points_channels(self, channel_ids: list[str]) -> None:
        """Set the ranking. Channels missing from the list keep their relative order after it."""
        async with self._session.begin() as s:
            rows = (await s.scalars(self._channels())).all()
            by_id = {r.channel_id: r for r in rows}
            ordered = [by_id[c] for c in channel_ids if c in by_id]
            ordered += [r for r in rows if r.channel_id not in set(channel_ids)]
            for i, r in enumerate(ordered):
                r.rank = i

    async def move_points_channel(self, channel_id: str, delta: int) -> None:
        ids = [p.channel_id for p in await self.points_channels()]
        if channel_id not in ids:
            return
        i = ids.index(channel_id)
        j = max(0, min(len(ids) - 1, i + delta))
        ids.insert(j, ids.pop(i))
        await self.reorder_points_channels(ids)

    async def set_balance(self, channel_id: str, balance: int) -> int | None:
        """Store a channel's balance. Returns the previous one."""
        async with self._session.begin() as s:
            row = await s.get(PointsChannelRow, (self._uid(), channel_id))
            if row is None:
                return None
            prev = row.balance
            row.balance = balance
            row.balance_at = datetime.now(UTC)
            return prev

    # --- Claim history ---------------------------------------------------------------------

    async def record(
        self,
        kind: str,
        *,
        amount: int = 0,
        channel_id: str | None = None,
        channel_name: str = "",
        campaign_id: str | None = None,
        detail: str = "",
        at: datetime | None = None,
    ) -> None:
        async with self._session.begin() as s:
            s.add(
                EventRow(
                    user_id=self._uid(),
                    at=at or datetime.now(UTC),
                    kind=kind,
                    amount=amount,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    campaign_id=campaign_id,
                    detail=detail,
                )
            )

    async def recent_events(
        self, limit: int = 50, kinds: Iterable[str] | None = None
    ) -> list[Event]:
        if self._user is None:
            return []
        q = (
            select(EventRow)
            .where(EventRow.user_id == self._user)
            .order_by(EventRow.at.desc(), EventRow.id.desc())
            .limit(limit)
        )
        if kinds is not None:
            q = q.where(EventRow.kind.in_(list(kinds)))
        async with self._session() as s:
            rows = (await s.scalars(q)).all()
            return [
                Event(
                    _utc(r.at),
                    r.kind,
                    r.channel_id,
                    r.channel_name,
                    r.campaign_id,
                    r.amount,
                    r.detail,
                )
                for r in rows
            ]

    async def count_events(self, kind: str) -> int:
        if self._user is None:
            return 0
        q = select(func.count()).where(EventRow.user_id == self._user, EventRow.kind == kind)
        async with self._session() as s:
            return int(await s.scalar(q) or 0)

    async def points_gained_since(self, since: datetime) -> dict[str, int]:
        """Channel Points gained per channel since a moment.

        "points" events are positive balance changes, so they already include Bonus Chests.
        """
        if self._user is None:
            return {}
        q = (
            select(EventRow.channel_id, func.sum(EventRow.amount))
            .where(EventRow.user_id == self._user, EventRow.kind == "points", EventRow.at >= since)
            .group_by(EventRow.channel_id)
        )
        async with self._session() as s:
            return {cid: int(total or 0) for cid, total in (await s.execute(q)).all() if cid}
