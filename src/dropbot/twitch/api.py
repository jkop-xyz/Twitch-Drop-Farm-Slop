"""The interface the farming loop uses. Implemented by TwitchClient and by tests' FakeTwitch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain import Campaign, Game, Stream


class TwitchError(Exception):
    pass


class AuthError(TwitchError):
    """The token is missing, expired, revoked, or for the wrong client."""


class TransientError(TwitchError):
    """Network trouble or a Twitch hiccup. Retry on the next tick."""


class ProtocolError(TwitchError):
    """Twitch rejected a request in a way that suggests its API changed. Needs a code fix."""

    def __init__(self, operation: str, message: str) -> None:
        super().__init__(f"{operation}: {message}")
        self.operation = operation
        self.message = message


@dataclass(frozen=True)
class TokenInfo:
    user_id: str
    login: str
    client_id: str


@dataclass(frozen=True)
class UserInfo:
    channel_id: str
    login: str
    display_name: str = ""


@dataclass(frozen=True)
class DropSession:
    drop_id: str
    current_minutes: int


@dataclass(frozen=True)
class PointsState:
    balance: int
    bonus_claim_id: str | None


class TwitchApi(Protocol):
    async def validate(self, token: str) -> TokenInfo: ...
    async def new_device_id(self) -> str: ...
    def authenticate(self, token: str, user_id: str, device_id: str) -> None: ...
    def deauthenticate(self) -> None: ...

    async def campaigns(self) -> list[Campaign]: ...
    async def live_streams(self, logins: list[str]) -> dict[str, Stream]: ...
    async def game_streams(self, game: Game, limit: int = 30) -> list[Stream]: ...
    async def lookup_user(self, login: str) -> UserInfo | None: ...
    async def followed_channels(self) -> list[UserInfo]: ...

    async def watch(self, stream: Stream) -> bool: ...
    async def send_minute_watched(self, stream: Stream) -> bool: ...
    async def current_drop(self, channel_id: str) -> DropSession | None: ...
    async def claim_drop(self, campaign_id: str, drop_id: str) -> bool: ...
    async def points(self, login: str) -> PointsState | None: ...
    async def claim_bonus(self, channel_id: str, claim_id: str) -> bool: ...

    async def close(self) -> None: ...
