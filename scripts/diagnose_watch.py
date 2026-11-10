"""Check what Twitch sees when the bot watches a stream: run inside the bot's container.

    docker compose exec -T dropbot python - <channel> < scripts/diagnose_watch.py

Uses the bot's stored login (from DATABASE_URL, or /data/bot.db) for one watch cycle: the
playback token, the stream playlist and its video segments, and the Drop progress Twitch
reports for that channel. Nothing is changed, claimed or stored. IP addresses are shown
with their last part hidden, so the output is safe to paste.
"""

import asyncio
import json
import os
import re
import sys
import time
from urllib.parse import urlsplit

from dropbot import version_label
from dropbot.store import Store
from dropbot.twitch import protocol
from dropbot.twitch.client import TwitchClient


def mask(ip: str | None) -> str:
    if not ip:
        return "(none)"
    if ":" in ip:  # IPv6: keep the first 3 groups
        return ":".join(ip.split(":")[:3]) + ":…"
    return ".".join(ip.split(".")[:3]) + ".x"


def info_field(playlist: str, key: str) -> str | None:
    m = re.search(rf'{key}="([^"]*)"', playlist)
    return m.group(1) if m else None


async def main(login: str) -> None:
    print(f"dropbot {version_label()}, checking channel {login!r}")
    store = Store(os.environ.get("DATABASE_URL"))
    account = await store.get_account()
    if account is None:
        sys.exit("No account is logged in.")
    c = TwitchClient()
    c.authenticate(account.access_token, account.user_id, account.device_id)
    try:
        await c.validate(account.access_token)
        print(f"login: OK ({account.login}, user id {account.user_id})")

        live = await c.live_streams([login])
        stream = next(iter(live.values()), None)
        if stream is None:
            sys.exit(f"{login} is not live.")
        print(
            f"stream: {stream.game_name or '(no game)'}, Drops on channel: "
            f"{'yes' if stream.drops_enabled else 'no'}"
        )

        data = await c._gql_data(protocol.gql_op("PlaybackAccessToken", login=stream.login))
        tok = data.get("streamPlaybackAccessToken")
        if not tok:
            sys.exit("PlaybackAccessToken: Twitch returned no token")
        value = json.loads(tok["value"])
        token_user = str(value.get("user_id") or "")
        dev = account.device_id or ""
        token_dev = str(value.get("device_id") or "")
        print(
            f"device id: stored {dev[:6] + '…' if dev else '(none!)'} ({len(dev)} chars), "
            f"in token {token_dev[:6] + '…' if token_dev else '(none!)'}"
            f"{'' if token_dev == dev else '  <-- DIFFERENT'}"
        )
        print(
            f"playback token: user_id={token_user or '(none!)'} "
            f"{'= your account' if token_user == account.user_id else '≠ YOUR ACCOUNT'}, "
            f"ip={mask(value.get('user_ip'))}, player={value.get('player_type')}, "
            f"platform={value.get('platform')}"
        )

        status, body = await c._request(
            "GET",
            protocol.USHER_URL.format(login=stream.login),
            params={"sig": tok["signature"], "token": tok["value"]},
        )
        master = body.decode("utf8", "replace")
        usher_ip = info_field(master, "USER-IP")
        same = "same as token" if usher_ip == value.get("user_ip") else "DIFFERENT from token"
        print(
            f"stream playlist: HTTP {status}, Twitch sees ip={mask(usher_ip)} ({same}), "
            f"cluster={info_field(master, 'CLUSTER')}"
        )
        server_time = info_field(master, "SERVER-TIME")
        if server_time:
            skew = time.time() - float(server_time)
            print(
                f"clock: {skew:+.1f}s compared with Twitch"
                f"{'  <-- check the server clock' if abs(skew) > 30 else ''}"
            )
        variants = [ln for ln in master.splitlines() if ln.startswith("http")]
        if status != 200 or not variants:
            sys.exit("No playable stream variants: " + master[:300])

        status, body = await c._request("GET", variants[-1], headers={"Connection": "close"})
        media = body.decode("utf8", "replace")
        chunks = [ln for ln in media.splitlines() if ln.startswith("http")]
        ads = media.count("stitched-ad")
        host = urlsplit(chunks[0]).hostname if chunks else "-"
        print(
            f"segment list: HTTP {status}, {len(chunks)} segments"
            f"{f', {ads} ad markers' if ads else ''}, host {host}"
        )
        codes = [(await c._request("HEAD", ch))[0] for ch in chunks]
        print(f"segment HEAD requests: {codes}")

        session = await c.current_drop(stream.channel_id)
        found = f"{session.drop_id} at {session.current_minutes} min" if session else "none"
        print(f"Drop progress on {stream.name}: {found}")
    finally:
        await c.close()
        await store.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python - <channel> < scripts/diagnose_watch.py")
    asyncio.run(main(sys.argv[1].lower()))
