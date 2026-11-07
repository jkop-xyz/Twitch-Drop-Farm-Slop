"""Turn Twitch GQL response data into domain objects. Pure functions, tested against fixtures.

A KeyError/TypeError here means Twitch changed a response shape; the client turns those into
ProtocolError.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..domain import Campaign, Drop, Game, Stream

Json = dict[str, Any]

_DIMS = re.compile(r"-\d+x\d+(?=\.(?:jpg|png|gif)$)", re.I)
_TOKEN_IN_URL = re.compile(r"access_token=([A-Za-z0-9]+)")
_BARE_TOKEN = re.compile(r"^[A-Za-z0-9]{20,64}$")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_token(pasted: str) -> str | None:
    """Accept the redirect URL (…#access_token=xyz&…) or a bare token."""
    pasted = pasted.strip()
    if m := _TOKEN_IN_URL.search(pasted):
        return m.group(1)
    if _BARE_TOKEN.match(pasted):
        return pasted
    return None


def slugify(name: str) -> str:
    slug = re.sub(r"\W+", "-", name.lower().replace("'", ""))
    return re.sub(r"-{2,}", "-", slug.strip("-"))


def parse_game(data: Json) -> Game:
    return Game(
        id=str(data["id"]),
        name=data.get("displayName") or data["name"],
        slug=data.get("slug") or "",
    )


def merge(primary: Json, secondary: Json) -> Json:
    """Deep-merge two campaign dicts, preferring primary's leaf values."""
    out: Json = {}
    for key in primary.keys() | secondary.keys():
        if key in primary and key in secondary:
            a, b = primary[key], secondary[key]
            out[key] = merge(a, b) if isinstance(a, dict) and isinstance(b, dict) else a
        elif key in primary:
            out[key] = primary[key]
        else:
            out[key] = secondary[key]
    return out


def claimed_benefits(inventory: Json) -> dict[str, datetime]:
    return {
        b["id"]: parse_ts(b["lastAwardedAt"])
        for b in (inventory.get("gameEventDrops") or [])
        if b.get("lastAwardedAt")
    }


def parse_drop(data: Json, claimed: dict[str, datetime]) -> Drop:
    starts, ends = parse_ts(data["startAt"]), parse_ts(data["endAt"])
    benefits = [e["benefit"] for e in (data.get("benefitEdges") or [])]
    me = data.get("self")
    if me:
        is_claimed = bool(me.get("isClaimed"))
        current = int(me.get("currentMinutesWatched") or 0)
    else:
        # No progress edge: infer "claimed" from benefits awarded inside the drop's window
        # (TwitchDropsMiner's heuristic).
        stamps = [claimed[b["id"]] for b in benefits if b["id"] in claimed]
        is_claimed = bool(stamps) and all(starts <= t < ends for t in stamps)
        current = 0
    required = int(data.get("requiredMinutesWatched") or 0)
    if is_claimed:
        current = required
    return Drop(
        id=data["id"],
        name=data.get("name") or "",
        required_minutes=required,
        current_minutes=current,
        claimed=is_claimed,
        starts_at=starts,
        ends_at=ends,
        precondition_ids=tuple(d["id"] for d in (data.get("preconditionDrops") or [])),
        rewards=tuple(b.get("name") or "" for b in benefits),
        image_url=benefits[0].get("imageAssetURL", "") if benefits else "",
    )


def parse_campaign(data: Json, claimed: dict[str, datetime]) -> Campaign:
    benefits = [
        e["benefit"]
        for d in data.get("timeBasedDrops") or []
        for e in (d.get("benefitEdges") or [])
    ]
    # Badge/emote rewards are delivered on Twitch itself, with no Account Link needed.
    badge_or_emote = any(b.get("distributionType") in ("BADGE", "EMOTE") for b in benefits)
    allow = data.get("allow") or {}
    channels = allow.get("channels")
    acl = bool(channels) and allow.get("isEnabled", True)
    allowed = frozenset(str(c["id"]) for c in channels) if acl else None
    allowed_logins = tuple(c["name"] for c in channels if c.get("name")) if acl else ()
    game = data["game"]
    return Campaign(
        id=data["id"],
        name=data.get("name") or "",
        game=parse_game(game),
        starts_at=parse_ts(data["startAt"]),
        ends_at=parse_ts(data["endAt"]),
        drops=tuple(parse_drop(d, claimed) for d in data.get("timeBasedDrops") or []),
        requires_account_link=not badge_or_emote,
        account_linked=bool((data.get("self") or {}).get("isAccountConnected")),
        link_url=data.get("accountLinkURL") or "",
        allowed_channel_ids=allowed,
        allowed_logins=allowed_logins,
        image_url=_DIMS.sub("", game.get("boxArtURL") or ""),
    )


def parse_stream_info(user: Json | None) -> tuple[Json | None, Stream | None]:
    """VideoPlayerStreamInfoOverlayChannel → (user dict, live Stream or None)."""
    if not user:
        return None, None
    s = user.get("stream")
    if not s:
        return user, None
    settings = user.get("broadcastSettings") or {}
    game = settings.get("game") or {}
    return user, Stream(
        channel_id=str(user["id"]),
        login=user["login"],
        display_name=user.get("displayName") or "",
        broadcast_id=str(s["id"]),
        game_id=str(game["id"]) if game.get("id") else None,
        game_name=game.get("displayName") or game.get("name") or "",
        viewers=int(s.get("viewersCount") or 0),
        drops_enabled=False,
        title=settings.get("title") or "",
    )


def parse_directory(data: Json, game: Game) -> list[Stream]:
    out = []
    for edge in ((data.get("game") or {}).get("streams") or {}).get("edges") or []:
        node = edge["node"]
        b = node.get("broadcaster")
        if b is None:
            continue
        out.append(
            Stream(
                channel_id=str(b["id"]),
                login=b["login"],
                display_name=b.get("displayName") or "",
                broadcast_id=str(node["id"]),
                game_id=game.id,
                game_name=game.name,
                viewers=int(node.get("viewersCount") or 0),
                drops_enabled=True,  # the directory query filters on DROPS_ENABLED
                title=node.get("title") or "",
            )
        )
    return out


def parse_points_context(data: Json) -> tuple[int, str | None, bool]:
    """ChannelPointsContext → (balance, available Bonus Chest claim id, points enabled)."""
    channel = data["community"]["channel"]
    settings = channel.get("communityPointsSettings") or {}
    enabled = settings.get("isEnabled") is not False
    cp = channel["self"]["communityPoints"]
    if cp is None and not enabled:
        return 0, None, False
    claim = cp.get("availableClaim")
    return int(cp["balance"]), (claim["id"] if claim and enabled else None), enabled
