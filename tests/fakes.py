"""An in-memory Twitch implementing TwitchApi, for end-to-end loop and web tests."""

from __future__ import annotations

from dataclasses import replace

from dropbot.domain import Campaign, Game, Stream
from dropbot.twitch import (
    AuthError,
    DropSession,
    PointsState,
    TokenInfo,
    UserInfo,
)
from dropbot.twitch.protocol import CLIENT_ID


class FakeTwitch:
    def __init__(self) -> None:
        self.tokens: dict[str, TokenInfo] = {
            "goodtoken0123456789abc": TokenInfo("42", "me", CLIENT_ID)
        }
        self.authed: str | None = None
        self.campaign_list: list[Campaign] = []
        self.streams: dict[str, Stream] = {}  # by channel id; present = live
        self.users: dict[str, UserInfo] = {}  # by login
        self.follows: list[UserInfo] = []
        self.balances: dict[str, int] = {}  # by login
        self.points_off: set[str] = set()
        self.avatars: dict[
            str, str
        ] = {}  # login -> profile picture URL  # logins with Channel Points turned off
        self.bonus: dict[str, str] = {}  # login -> claim id
        self.drop_sessions: dict[str, DropSession] = {}  # channel id -> session
        self.claimed_drops: list[tuple[str, str]] = []
        self.claimed_bonuses: list[tuple[str, str]] = []
        self.watched: list[str] = []
        self.spade: list[str] = []
        self.fail_next: Exception | None = None
        self.spade_error: Exception | None = None
        self.calls: list[str] = []

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_next is not None:
            e, self.fail_next = self.fail_next, None
            raise e

    # helpers for tests
    def go_live(self, s: Stream) -> None:
        self.streams[s.channel_id] = s
        self.users.setdefault(s.login, UserInfo(s.channel_id, s.login, s.display_name))

    def go_offline(self, channel_id: str) -> None:
        self.streams.pop(channel_id, None)

    def add_user(self, channel_id: str, login: str) -> None:
        self.users[login] = UserInfo(channel_id, login, login.title())

    # TwitchApi
    async def validate(self, token: str) -> TokenInfo:
        self._maybe_fail("validate")
        if token not in self.tokens:
            raise AuthError("invalid token")
        return self.tokens[token]

    async def new_device_id(self) -> str:
        return "fakedevice"

    def authenticate(self, token: str, user_id: str, device_id: str) -> None:
        self.authed = token

    def deauthenticate(self) -> None:
        self.authed = None

    async def campaigns(self) -> list[Campaign]:
        self._maybe_fail("campaigns")
        claimed = set(self.claimed_drops)
        return [
            replace(
                c,
                drops=tuple(
                    replace(d, claimed=True) if (c.id, d.id) in claimed else d for d in c.drops
                ),
            )
            for c in self.campaign_list
        ]

    async def live_streams(self, logins: list[str]) -> dict[str, Stream]:
        self._maybe_fail("live_streams")
        wanted = {x.lower() for x in logins}
        return {cid: s for cid, s in self.streams.items() if s.login in wanted}

    async def game_streams(self, game: Game, limit: int = 30) -> list[Stream]:
        self._maybe_fail("game_streams")
        found = [s for s in self.streams.values() if s.game_id == game.id and s.drops_enabled]
        return sorted(found, key=lambda s: -s.viewers)[:limit]

    async def lookup_user(self, login: str) -> UserInfo | None:
        return self.users.get(login.lower())

    async def followed_channels(self) -> list[UserInfo]:
        return list(self.follows)

    async def watch(self, stream: Stream) -> bool:
        self.watched.append(stream.channel_id)
        return stream.channel_id in self.streams

    async def send_minute_watched(self, stream: Stream) -> bool:
        if self.spade_error is not None:
            raise self.spade_error
        self.spade.append(stream.channel_id)
        return True

    async def current_drop(self, channel_id: str) -> DropSession | None:
        self._maybe_fail("current_drop")
        return self.drop_sessions.get(channel_id)

    async def claim_drop(self, campaign_id: str, drop_id: str) -> bool:
        self.claimed_drops.append((campaign_id, drop_id))
        return True

    async def points(self, login: str) -> PointsState | None:
        if login not in self.balances:
            return None
        pic = self.avatars.get(login)
        if login in self.points_off:
            return PointsState(self.balances[login], None, enabled=False, avatar_url=pic)
        return PointsState(self.balances[login], self.bonus.get(login), avatar_url=pic)

    async def points_many(self, logins: list[str]) -> dict[str, PointsState | None]:
        self.calls.append("points_many")
        return {login: await self.points(login) for login in logins}

    async def claim_bonus(self, channel_id: str, claim_id: str) -> bool:
        self.claimed_bonuses.append((channel_id, claim_id))
        for login, cid in list(self.bonus.items()):
            if cid == claim_id:
                del self.bonus[login]
                self.balances[login] += 50
        return True

    async def close(self) -> None:
        pass
