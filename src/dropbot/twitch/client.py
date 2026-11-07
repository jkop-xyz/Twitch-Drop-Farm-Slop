"""The real Twitch client. The only code that speaks Twitch's private API."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import secrets
from base64 import b64encode
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import aiohttp

from ..domain import Campaign, Game, Stream
from . import parse, protocol
from .api import (
    AuthError,
    DropSession,
    PointsState,
    ProtocolError,
    TokenInfo,
    TransientError,
    UserInfo,
)

log = logging.getLogger(__name__)

Json = dict[str, Any]


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class TwitchClient:
    def __init__(self, *, retries: int = 3) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._retries = retries
        self._token: str | None = None
        self._user_id: str | None = None
        self._device_id: str = ""
        self._session_id = secrets.token_hex(8)
        self._gql_limit = asyncio.Semaphore(5)
        # broadcast_id -> lowest-quality HLS playlist URL
        self._playlists: dict[str, str] = {}
        # login -> spade URL
        self._spade: dict[str, str] = {}

    # --- plumbing --------------------------------------------------------------------------

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": protocol.USER_AGENT},
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def _request(self, method: str, url: str, **kw: Any) -> tuple[int, bytes]:
        """HTTP with retries on connection errors and 5xx/429. Returns (status, body)."""
        session = await self._http()
        delay = 1.0
        last: Exception | None = None
        for _ in range(self._retries):
            try:
                async with session.request(method, url, **kw) as resp:
                    body = await resp.read()
                    if resp.status < 500 and resp.status != 429:
                        return resp.status, body
                    last = TransientError(f"{method} {url.split('?')[0]}: HTTP {resp.status}")
            except (aiohttp.ClientError, TimeoutError) as e:
                last = e
            await asyncio.sleep(delay)
            delay *= 3
        raise TransientError(str(last))

    def _gql_headers(self) -> dict[str, str]:
        if not self._token:
            raise AuthError("not logged in")
        h = {
            "Accept": "*/*",
            "Accept-Language": "en-US",
            "Client-Id": protocol.CLIENT_ID,
            "Client-Session-Id": self._session_id,
            "Origin": protocol.CLIENT_URL,
            "Referer": protocol.CLIENT_URL,
            "Authorization": f"OAuth {self._token}",
        }
        if self._device_id:
            h["X-Device-Id"] = self._device_id
        return h

    async def _gql(self, ops: Json | list[Json]) -> Any:
        """Run one operation (→ dict) or a batch (→ list), classifying errors."""
        batch = isinstance(ops, list)
        names = ",".join(o["operationName"] for o in (ops if batch else [ops]))
        retried_pqnf = False
        delay = 2.0
        for attempt in range(self._retries + 1):
            async with self._gql_limit:
                status, body = await self._request(
                    "POST", protocol.GQL_URL, json=ops, headers=self._gql_headers()
                )
            if status == 401:
                raise AuthError(f"{names}: HTTP 401")
            if status >= 400:
                raise ProtocolError(names, f"HTTP {status}: {body[:200]!r}")
            try:
                data = json.loads(body)
            except ValueError as e:
                raise ProtocolError(names, "non-JSON response") from e
            responses = data if isinstance(data, list) else [data]
            retry = False
            for r in responses:
                if "error" in r:
                    if r.get("status") == 401:
                        raise AuthError(f"{names}: {r.get('message')}")
                    raise ProtocolError(names, f"{r['error']}: {r.get('message')}")
                for err in r.get("errors") or []:
                    msg = err.get("message", "")
                    if msg == "PersistedQueryNotFound" and not retried_pqnf:
                        retried_pqnf = retry = True
                    elif msg in protocol.TRANSIENT_GQL_ERRORS:
                        retry = True
                    else:
                        raise ProtocolError(names, msg or str(err))
            if not retry:
                return data
            if attempt < self._retries:
                await asyncio.sleep(delay)
                delay *= 2
        raise TransientError(f"{names}: still failing after retries")

    async def _gql_data(self, op: Json) -> Json:
        resp = await self._gql(op)
        data = resp.get("data")
        if data is None:
            raise ProtocolError(op["operationName"], "response has no data")
        return data

    async def _gql_batch(self, ops: list[Json]) -> list[Json]:
        out: list[Json] = []
        for chunk in _chunks(ops, protocol.GQL_BATCH):
            for r in await self._gql(chunk):
                out.append(r.get("data") or {})
        return out

    # --- auth ------------------------------------------------------------------------------

    async def validate(self, token: str) -> TokenInfo:
        status, body = await self._request(
            "GET", protocol.VALIDATE_URL, headers={"Authorization": f"OAuth {token}"}
        )
        if status == 401:
            raise AuthError("token rejected by Twitch")
        if status != 200:
            raise TransientError(f"validate: HTTP {status}")
        data = json.loads(body)
        info = TokenInfo(str(data["user_id"]), data["login"], data["client_id"])
        if info.client_id != protocol.CLIENT_ID:
            raise AuthError(
                f"token belongs to client {info.client_id}, expected the Android app client"
            )
        return info

    async def new_device_id(self) -> str:
        """Twitch hands out a device id as the 'unique_id' cookie on its home page."""
        session = await self._http()
        try:
            await self._request(
                "GET", protocol.CLIENT_URL, headers={"User-Agent": protocol.BROWSER_USER_AGENT}
            )
            cookie = session.cookie_jar.filter_cookies(protocol.CLIENT_URL).get("unique_id")
            if cookie is not None and cookie.value:
                return cookie.value
        except TransientError:
            pass
        return secrets.token_hex(16)

    def authenticate(self, token: str, user_id: str, device_id: str) -> None:
        self._token, self._user_id, self._device_id = token, user_id, device_id

    def deauthenticate(self) -> None:
        self._token = self._user_id = None

    # --- campaigns -------------------------------------------------------------------------

    async def campaigns(self) -> list[Campaign]:
        inv = (await self._gql_data(protocol.gql_op("Inventory")))["currentUser"]["inventory"]
        claimed = parse.claimed_benefits(inv)
        in_progress = {c["id"]: c for c in inv.get("dropCampaignsInProgress") or []}

        dash = (await self._gql_data(protocol.gql_op("Campaigns")))["currentUser"]
        listed = {
            c["id"]: c
            for c in dash.get("dropCampaigns") or []
            if c.get("status") in ("ACTIVE", "UPCOMING")
        }
        details = await self._gql_batch(
            [
                protocol.gql_op("CampaignDetails", channelLogin=self._user_id, dropID=cid)
                for cid in listed
            ]
        )
        for d in details:
            dc = (d.get("user") or {}).get("dropCampaign")
            if dc and dc.get("id") in listed:
                listed[dc["id"]] = parse.merge(listed[dc["id"]], dc)

        merged = dict(listed)
        for cid, c in in_progress.items():
            merged[cid] = parse.merge(c, merged[cid]) if cid in merged else c

        out = []
        for data in merged.values():
            if not data.get("game"):
                continue
            try:
                out.append(parse.parse_campaign(data, claimed))
            except (KeyError, TypeError, ValueError) as e:
                raise ProtocolError("DropCampaignDetails", f"unexpected shape: {e!r}") from e
        return out

    # --- streams ---------------------------------------------------------------------------

    async def _stream_infos(self, logins: list[str]) -> list[tuple[Json | None, Stream | None]]:
        datas = await self._gql_batch([protocol.gql_op("StreamInfo", channel=x) for x in logins])
        try:
            return [parse.parse_stream_info(d.get("user")) for d in datas]
        except (KeyError, TypeError) as e:
            raise ProtocolError("VideoPlayerStreamInfoOverlayChannel", repr(e)) from e

    async def live_streams(self, logins: list[str]) -> dict[str, Stream]:
        logins = list(dict.fromkeys(x.lower() for x in logins))
        if not logins:
            return {}
        live = {s.channel_id: s for _, s in await self._stream_infos(logins) if s is not None}
        if not live:
            return live
        avail = await self._gql_batch(
            [protocol.gql_op("AvailableDrops", channelID=cid) for cid in live]
        )
        with_drops = {
            str(ch["id"])
            for d in avail
            if (ch := d.get("channel")) and ch.get("viewerDropCampaigns")
        }
        return {cid: replace(s, drops_enabled=cid in with_drops) for cid, s in live.items()}

    async def game_streams(self, game: Game, limit: int = 30) -> list[Stream]:
        slug = game.slug or parse.slugify(game.name)
        data = await self._gql_data(protocol.gql_op("GameDirectory", slug=slug, limit=limit))
        try:
            return parse.parse_directory(data, game)
        except (KeyError, TypeError) as e:
            raise ProtocolError("DirectoryPage_Game", repr(e)) from e

    async def lookup_user(self, login: str) -> UserInfo | None:
        [(user, _)] = await self._stream_infos([login.strip().lower()])
        if not user:
            return None
        return UserInfo(str(user["id"]), user["login"], user.get("displayName") or "")

    async def followed_channels(self) -> list[UserInfo]:
        out: list[UserInfo] = []
        cursor = ""
        for _ in range(50):  # 5000 follows is plenty
            data = await self._gql_data(protocol.gql_op("ChannelFollows", cursor=cursor))
            try:
                follows = data["user"]["follows"]
                for edge in follows["edges"]:
                    n = edge["node"]
                    out.append(
                        UserInfo(str(n.get("id") or ""), n["login"], n.get("displayName") or "")
                    )
                    cursor = edge.get("cursor") or ""
                if not follows["pageInfo"]["hasNextPage"]:
                    break
            except (KeyError, TypeError) as e:
                raise ProtocolError("ChannelFollows", repr(e)) from e
        return out

    # --- watching --------------------------------------------------------------------------

    async def _playlist_url(self, stream: Stream) -> str | None:
        if url := self._playlists.get(stream.broadcast_id):
            return url
        data = await self._gql_data(protocol.gql_op("PlaybackAccessToken", login=stream.login))
        tok = data.get("streamPlaybackAccessToken")
        if not tok:
            return None
        status, body = await self._request(
            "GET",
            protocol.USHER_URL.format(login=stream.login),
            params={"sig": tok["signature"], "token": tok["value"]},
        )
        text = body.decode("utf8", "replace").strip()
        if status != 200 or not text.startswith("#EXTM3U"):
            return None
        variants = [line for line in text.splitlines() if line.startswith("http")]
        if not variants:
            return None
        self._playlists[stream.broadcast_id] = variants[-1]  # last = lowest quality
        return variants[-1]

    async def watch(self, stream: Stream) -> bool:
        """Fetch the chunk list and HEAD each chunk. Advances Drops without downloading video."""
        url = await self._playlist_url(stream)
        if url is None:
            return False
        status, body = await self._request("GET", url, headers={"Connection": "close"})
        if status >= 400:
            self._playlists.pop(stream.broadcast_id, None)
            return False
        chunks = [ln for ln in body.decode("utf8", "replace").splitlines() if ln.startswith("http")]
        if not chunks:
            return False
        for chunk in chunks:
            status, _ = await self._request("HEAD", chunk)
            if status != 200:
                return False
        return True

    async def _spade_url(self, login: str) -> str | None:
        if url := self._spade.get(login):
            return url
        ua = {"User-Agent": protocol.BROWSER_USER_AGENT}
        _, html = await self._request("GET", f"{protocol.CLIENT_URL}/{login}", headers=ua)
        text = html.decode("utf8", "replace")
        m = re.search(protocol.SPADE_PATTERN, text, re.I)
        if not m and (s := re.search(protocol.SETTINGS_JS_PATTERN, text, re.I)):
            _, js = await self._request("GET", s.group(1), headers=ua)
            m = re.search(protocol.SPADE_PATTERN, js.decode("utf8", "replace"), re.I)
        if not m:
            return None
        self._spade[login] = m.group(1)
        return m.group(1)

    async def send_minute_watched(self, stream: Stream) -> bool:
        url = await self._spade_url(stream.login)
        if url is None:
            return False
        payload = [
            {
                "event": "minute-watched",
                "properties": {
                    "broadcast_id": stream.broadcast_id,
                    "channel_id": stream.channel_id,
                    "channel": stream.login,
                    "client_time": datetime.now(UTC).isoformat(),
                    "game": stream.game_name,
                    "game_id": stream.game_id or "",
                    "hidden": False,
                    "is_live": True,
                    "live": True,
                    "logged_in": True,
                    "minutes_logged": 1,
                    "muted": False,
                    # Channel Points are only credited for events from a known player; every
                    # maintained points miner reports the web player. Without it Twitch still
                    # answers 204 but the minute earns no points.
                    "player": "site",
                    "user_id": self._user_id,
                },
            }
        ]
        encoded = b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        # Sent like the web player sends it (the event claims player=site), not as the app.
        status, _ = await self._request(
            "POST",
            url,
            data={"data": encoded},
            headers={"User-Agent": protocol.BROWSER_USER_AGENT},
        )
        if status != 204:
            self._spade.pop(stream.login, None)
        return status == 204

    async def current_drop(self, channel_id: str) -> DropSession | None:
        data = await self._gql_data(protocol.gql_op("CurrentDrop", channelID=channel_id))
        s = (data.get("currentUser") or {}).get("dropCurrentSession")
        if not s or not s.get("dropID"):
            return None
        return DropSession(s["dropID"], int(s.get("currentMinutesWatched") or 0))

    # --- claims & points -------------------------------------------------------------------

    async def claim_drop(self, campaign_id: str, drop_id: str) -> bool:
        instance = f"{self._user_id}#{campaign_id}#{drop_id}"
        data = await self._gql_data(
            protocol.gql_op("ClaimDrop", input={"dropInstanceID": instance})
        )
        result = data.get("claimDropRewards") or {}
        return result.get("status") in ("ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED")

    @staticmethod
    def _points_state(data: Json) -> PointsState | None:
        if not data.get("community") or not data["community"].get("channel"):
            return None
        avatar = data["community"].get("profileImageURL") or None
        return PointsState(*parse.parse_points_context(data), avatar_url=avatar)

    async def points(self, login: str) -> PointsState | None:
        data = await self._gql_data(protocol.gql_op("ChannelPointsContext", channelLogin=login))
        return self._points_state(data)

    async def points_many(self, logins: list[str]) -> dict[str, PointsState | None]:
        """Balances for many channels, batched (GQL_BATCH per request)."""
        logins = list(dict.fromkeys(logins))
        datas = await self._gql_batch(
            [protocol.gql_op("ChannelPointsContext", channelLogin=x) for x in logins]
        )
        return {login: self._points_state(d) for login, d in zip(logins, datas, strict=True)}

    async def claim_bonus(self, channel_id: str, claim_id: str) -> bool:
        data = await self._gql_data(
            protocol.gql_op(
                "ClaimCommunityPoints", input={"claimID": claim_id, "channelID": channel_id}
            )
        )
        result = data.get("claimCommunityPoints")
        return bool(result) and not result.get("error")


def _shape_checked(name: str, fn):
    """A KeyError/TypeError/IndexError while reading a response means Twitch changed its shape."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (KeyError, TypeError, IndexError) as e:
            raise ProtocolError(name, f"unexpected response shape: {e!r}") from e

    return wrapper


for _name in (
    "validate",
    "campaigns",
    "live_streams",
    "game_streams",
    "lookup_user",
    "followed_channels",
    "watch",
    "send_minute_watched",
    "current_drop",
    "claim_drop",
    "points",
    "points_many",
    "claim_bonus",
):
    setattr(TwitchClient, _name, _shape_checked(_name, getattr(TwitchClient, _name)))
