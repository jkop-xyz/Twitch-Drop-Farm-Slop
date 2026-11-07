from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AccountRow(Base):
    """The single logged-in Account (id is always 1)."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_token: Mapped[str] = mapped_column(String(200))
    user_id: Mapped[str] = mapped_column(String(32))
    login: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(100), default="")
    device_id: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CampaignStateRow(Base):
    """What the owner has decided about a Drop Campaign the bot has seen."""

    __tablename__ = "campaign_state"

    # Per-account: the Twitch user id this row belongs to.
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    game_name: Mapped[str] = mapped_column(String(300), default="")
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)


class PointsChannelRow(Base):
    __tablename__ = "points_channel"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    login: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(100), default="")
    rank: Mapped[int] = mapped_column(Integer)
    balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    balance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Whether the channel has Channel Points turned on. None until the bot has checked.
    points_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(300), nullable=True)


class EventRow(Base):
    """Claim history: Drops claimed, Bonus Chests claimed, Channel Points gained."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    channel_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    channel_name: Mapped[str] = mapped_column(String(100), default="")
    campaign_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    detail: Mapped[str] = mapped_column(Text, default="")
