"""Run the real web UI against a fake Twitch filled with made-up data.

For UI work and for the README screenshots (scripts/screenshots.mjs). No Twitch account is
used and nothing is sent to Twitch: the channels, avatars and box art are all invented.

    .venv/bin/python scripts/demo.py [port]      # default 8095, data in a temp dir
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from dropbot.domain import Campaign, Drop, Game  # noqa: E402
from dropbot.farmer import Farmer  # noqa: E402
from dropbot.store import Store  # noqa: E402
from dropbot.twitch import UserInfo  # noqa: E402
from dropbot.twitch.api import DropSession  # noqa: E402
from dropbot.web.app import create_app  # noqa: E402
from tests.factories import stream  # noqa: E402
from tests.fakes import FakeTwitch  # noqa: E402

NOW = datetime.now(UTC)


def _svg(body: str, view: str) -> str:
    return "data:image/svg+xml," + quote(
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{view}'>{body}</svg>"
    )


def art(h1: int, h2: int, label: str) -> str:
    """Box art / reward art: a gradient with a label."""
    return _svg(
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='hsl({h1} 70% 55%)'/>"
        f"<stop offset='1' stop-color='hsl({h2} 70% 30%)'/></linearGradient></defs>"
        "<rect width='60' height='80' fill='url(#g)'/>"
        "<text x='30' y='48' font-size='22' text-anchor='middle' fill='white' "
        f"font-family='sans-serif' font-weight='bold'>{label}</text>",
        "0 0 60 80",
    )


SHAPES = [
    "<circle cx='35' cy='35' r='15' fill='white' opacity='.9'/>",
    "<path d='M35 17 L53 50 H17 Z' fill='white' opacity='.9'/>",
    "<rect x='22' y='22' width='26' height='26' rx='5' fill='white' opacity='.9' "
    "transform='rotate(45 35 35)'/>",
    "<path d='M35 15 l5.9 12 13.1 1.9-9.5 9.3 2.2 13.1L35 45.1 23.3 51.3l2.2-13.1-9.5-9.3 "
    "13.1-1.9z' fill='white' opacity='.9'/>",
    "<path d='M20 42 a15 15 0 0 1 30 0 z M27 27 h16 v6 h-16z' fill='white' opacity='.9'/>",
]


def avatar(i: int, hue: int) -> str:
    """A made-up profile picture: a gradient disc with a white shape."""
    return _svg(
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='hsl({hue} 75% 62%)'/>"
        f"<stop offset='1' stop-color='hsl({hue + 40} 70% 38%)'/></linearGradient></defs>"
        f"<rect width='70' height='70' fill='url(#g)'/>{SHAPES[i % len(SHAPES)]}",
        "0 0 70 70",
    )


def drop(id, reward, required, current=0, claimed=False) -> Drop:
    h = sum(map(ord, reward)) % 360
    return Drop(
        id=id,
        name=reward,
        required_minutes=required,
        current_minutes=current,
        claimed=claimed,
        rewards=(reward,),
        image_url=art(h, h + 60, reward[:1]),
    )


def campaign(id, game, name, ends_days, drops, *, linked=True, needs_link=True, allowed=None):
    return Campaign(
        id=id,
        name=name,
        game=game,
        image_url=BOX[game.id],
        starts_at=NOW - timedelta(days=3),
        ends_at=NOW + timedelta(days=ends_days),
        drops=tuple(drops),
        requires_account_link=needs_link,
        account_linked=linked,
        link_url="https://example.com/link",
        allowed_channel_ids=frozenset(allowed) if allowed else None,
    )


RUST, GENSHIN = Game("1", "Rust"), Game("2", "Genshin Impact")
WOW, SIEGE = Game("3", "World of Warcraft"), Game("4", "Tom Clancy's Rainbow Six Siege")
BOX = {
    "1": art(20, 0, "R"),
    "2": art(200, 260, "G"),
    "3": art(45, 20, "W"),
    "4": art(210, 230, "R6"),
}

# (channel id, login, display name, balance, live game, viewers, points on)
CHANNELS = [
    ("c1", "pixelpaladin", "PixelPaladin", 6110, WOW, 17010, True),
    ("c2", "novarush", "NovaRush", 0, RUST, 1330, False),
    ("c3", "cozycrafter", "CozyCrafter", 7220, WOW, 5400, True),
    ("c4", "lagwizard", "LagWizard", 0, None, 0, False),
    ("c5", "midnightmara", "MidnightMara", 1250, None, 0, True),
    ("c6", "byteknight", "ByteKnight", 300, None, 0, True),
    ("c7", "quietquasar", "QuietQuasar", 940, None, 0, True),
    ("c8", "turbotortoise", "TurboTortoise", 18420, None, 0, True),
]


async def main() -> None:
    data = Path(tempfile.mkdtemp(prefix="dropbot-demo-"))
    store = Store(f"sqlite:///{data / 'demo.db'}")
    await store.migrate()
    tw = FakeTwitch()
    tw.campaign_list = [
        campaign(
            "rust1",
            RUST,
            "Rust Drops Wave 42",
            2,
            [
                drop("r1", "Garage Door Skin", 120, 87),
                drop("r2", "Hazmat Suit", 240),
                drop("r3", "AK-47 Skin", 360),
            ],
            needs_link=False,
        ),
        campaign(
            "gen1",
            GENSHIN,
            "V7.2 Genshin Impact Drops",
            16,
            [
                drop("g1", "Primogem×20", 30),
                drop("g2", "Mora×20000", 45),
                drop("g3", "Hero's Wit×3", 90),
            ],
            linked=False,
        ),
        campaign(
            "wow1",
            WOW,
            "Midnight Twitch Drops",
            9,
            [drop("w1", "Felstorm Mount", 240, 240, True), drop("w2", "Pet: Whelpling", 120, 30)],
        ),
        campaign(
            "r6a",
            SIEGE,
            "Siege X Charm Drops",
            5,
            [drop("s1", "Glacier Charm", 60), drop("s2", "Sub reward", 0)],
        ),
        campaign(
            "r6b",
            SIEGE,
            "Pro League Viewer Rewards",
            12,
            [drop("p1", "Pro League Pack", 180)],
            allowed={"c9"},
        ),
    ]
    # The biggest Rust stream: not on the list, so it gets the Drop slot by viewer count.
    tw.go_live(
        replace(
            stream("c9", game=RUST, viewers=21000), login="siegequeen", display_name="SiegeQueen"
        )
    )
    tw.balances["siegequeen"] = 40
    tw.avatars["siegequeen"] = avatar(4, 330)
    tw.drop_sessions["c9"] = DropSession("r1", 87)

    farmer = Farmer(store, tw)
    await farmer.login("goodtoken0123456789abc")
    for i, (cid, login, name, balance, game, viewers, points_on) in enumerate(CHANNELS):
        tw.users[login] = UserInfo(cid, login, name)
        tw.balances[login] = balance
        tw.avatars[login] = avatar(i, (i * 47 + 200) % 360)
        if not points_on:
            tw.points_off.add(login)
        if game:
            tw.go_live(
                replace(stream(cid, game=game, viewers=viewers), login=login, display_name=name)
            )
        await store.add_points_channel(cid, login, name)
    # The Drop stream is on the list too, so its one Watch Slot earns the Drop and points.
    await store.add_points_channel("c9", "siegequeen", "SiegeQueen")
    tw.users["starlitsam"] = UserInfo("c10", "starlitsam", "StarlitSam")  # for the balance check
    tw.balances["starlitsam"] = 2350
    tw.avatars["starlitsam"] = avatar(3, 50)
    tw.bonus["pixelpaladin"] = "claim1"

    await farmer.tick()
    for cid in ("rust1", "gen1", "wow1"):
        await store.set_selected(cid, True)
    await farmer.tick()
    await store.record(
        "drop",
        campaign_id="wow1",
        detail="Felstorm Mount",
        channel_name="CozyCrafter",
        at=NOW - timedelta(hours=3),
    )
    await store.record(
        "bonus", channel_id="c1", channel_name="PixelPaladin", at=NOW - timedelta(minutes=9)
    )
    for i in range(12):
        await store.record(
            "points",
            channel_id="c1",
            channel_name="PixelPaladin",
            amount=10,
            at=NOW - timedelta(minutes=5 * i),
        )
    await store.record(
        "points",
        channel_id="c3",
        channel_name="CozyCrafter",
        amount=560,
        at=NOW - timedelta(days=2),
    )
    # One campaign that appeared since the last visit, shown with a "new" badge.
    tw.campaign_list.append(
        campaign(
            "new1",
            RUST,
            "Rust Twitch Drops: Halloween",
            20,
            [drop("n1", "Pumpkin Hat", 60)],
            needs_link=False,
        )
    )
    farmer.wake(refresh_campaigns=True)
    await farmer.tick()

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8095
    print(f"demo UI on http://127.0.0.1:{port}  (data in {data})", flush=True)
    config = uvicorn.Config(create_app(farmer), host="127.0.0.1", port=port, log_level="warning")
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    asyncio.run(main())
