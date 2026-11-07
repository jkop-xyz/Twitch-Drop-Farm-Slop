from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dropbot.domain import Campaign, Drop, Game, PointsChannel, Stream

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
GAME = Game("1001", "Some Game", "some-game")
OTHER_GAME = Game("2002", "Other Game", "other-game")


def drop(id="d1", required=60, current=0, claimed=False, **kw) -> Drop:
    return Drop(
        id=id,
        name=f"Drop {id}",
        required_minutes=required,
        current_minutes=current,
        claimed=claimed,
        **kw,
    )


def campaign(
    id="c1",
    *,
    game=GAME,
    ends_in=timedelta(days=3),
    started_ago=timedelta(days=1),
    drops=None,
    requires_link=True,
    linked=True,
    allowed=None,
) -> Campaign:
    return Campaign(
        id=id,
        name=f"Campaign {id}",
        game=game,
        starts_at=NOW - started_ago,
        ends_at=NOW + ends_in,
        drops=tuple(drops if drops is not None else [drop(f"{id}-d1")]),
        requires_account_link=requires_link,
        account_linked=linked,
        allowed_channel_ids=frozenset(allowed) if allowed is not None else None,
    )


def stream(channel_id, *, game=GAME, viewers=100, drops_enabled=True) -> Stream:
    return Stream(
        channel_id=channel_id,
        login=f"user{channel_id}",
        broadcast_id=f"b{channel_id}",
        game_id=game.id if game else None,
        game_name=game.name if game else "",
        viewers=viewers,
        drops_enabled=drops_enabled,
    )


def points(*channel_ids: str) -> list[PointsChannel]:
    return [PointsChannel(cid, f"user{cid}", rank=i) for i, cid in enumerate(channel_ids)]


def live(*streams: Stream) -> dict[str, Stream]:
    return {s.channel_id: s for s in streams}
