"""Web UI: FastAPI + Jinja + htmx. No auth by design (docs/adr/0001)."""

from __future__ import annotations

import zlib
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import REVISION, __version__, version_label
from ..domain import Campaign, DropReason
from ..farmer import Farmer
from ..scheduler import competes
from ..store import NoAccount
from ..twitch import AuthError, TwitchError
from ..twitch.protocol import LOGIN_URL

HERE = Path(__file__).parent


def _humanize(delta: timedelta) -> str:
    secs = int(abs(delta.total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{mins}m"


def _minutes(n: int) -> str:
    """Minutes as "45 min" / "2h 5m"."""
    n = max(0, int(n))
    return f"{n} min" if n < 60 else _humanize(timedelta(minutes=n))


def _hue(text: str) -> int:
    """A stable colour for an avatar, derived from a name."""
    return zlib.crc32(text.lower().encode()) % 360


SECURITY_HEADERS = {
    # No framing: otherwise another site could overlay the UI and trick clicks (clickjacking),
    # which would also get past the cross-site POST check.
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    # Don't leak the bot's LAN address to Twitch or anywhere else when following links.
    "Referrer-Policy": "no-referrer",
}


def is_cross_site(request: Request) -> bool:
    """True if a browser tells us the request came from another site.

    Prefers Sec-Fetch-Site (set by the browser itself, so it survives reverse proxies that
    rewrite Host). Falls back to comparing Origin with Host. Requests with neither header
    (curl, scripts) are allowed.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return origin == "null"
    return urlsplit(origin).netloc != request.headers.get("host", "")


def create_app(farmer: Farmer, *, lifespan=None) -> FastAPI:
    app = FastAPI(
        title="Twitch Drop Bot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["version"] = __version__
    templates.env.globals["version_label"] = version_label()
    store = farmer.store

    @app.exception_handler(NoAccount)
    async def not_logged_in(_request: Request, _exc: NoAccount):
        return back("/login", "Log in first.")

    @app.middleware("http")
    async def reject_cross_site_posts(request: Request, call_next):
        """The UI has no login (ADR 0001), so stop other websites from submitting its forms."""
        if request.method == "POST" and is_cross_site(request):
            return PlainTextResponse("Cross-site request rejected.", status_code=403)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def until(dt: datetime | None) -> str:
        if dt is None:
            return ""
        delta = dt - farmer.clock()
        if abs(delta.total_seconds()) < 60:
            return "just now" if delta.total_seconds() <= 0 else "in under a minute"
        return (
            ("in " if delta.total_seconds() >= 0 else "")
            + _humanize(delta)
            + (" ago" if delta.total_seconds() < 0 else "")
        )

    def num(n: int | None) -> str:
        return "–" if n is None else f"{n:,}"

    templates.env.filters["until"] = until
    templates.env.filters["num"] = num
    templates.env.filters["minutes"] = _minutes
    templates.env.filters["hue"] = _hue

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("state", farmer.state)
        ctx.setdefault("page", name.split(".")[0])
        return templates.TemplateResponse(request, name, ctx)

    def back(url: str, error: object = None) -> RedirectResponse:
        if error:
            url += "?error=" + quote(str(error))
        return RedirectResponse(url, status_code=303)

    async def dashboard_ctx() -> dict:
        st = farmer.state
        campaigns = {c.id: c for c in st.campaigns}
        channels = await store.points_channel_info()
        avatars = {c.channel.channel_id: c.avatar_url for c in channels if c.avatar_url}
        slots = []
        for s in st.plan.slots:
            entry = {
                "stream": s.stream,
                "campaign": None,
                "drop": None,
                "points": not isinstance(s.reason, DropReason) or s.reason.also_points,
                "balance": st.slot_balances.get(s.stream.channel_id),
                "points_off": s.stream.channel_id in st.slot_points_off,
                "stalled_since": st.drop_stalled_since
                if isinstance(s.reason, DropReason)
                else None,
                "avatar_url": st.avatars.get(s.stream.channel_id)
                or avatars.get(s.stream.channel_id),
            }
            if isinstance(s.reason, DropReason) and (c := campaigns.get(s.reason.campaign_id)):
                entry["campaign"] = c
                earnable = c.earnable_drops(farmer.clock())
                entry["drop"] = min(
                    earnable, key=lambda d: d.required_minutes - d.current_minutes, default=None
                )
            slots.append(entry)
        week = await store.points_gained_since(farmer.clock() - timedelta(days=7))
        return {
            "slots": slots,
            "channels": channels,
            "week": week,
            # Points gains are summarised per channel; listing them would bury the claims.
            "events": await store.recent_events(limit=20, kinds=["drop", "bonus"]),
            "campaign_names": {c.id: f"{c.game.name}: {c.name}" for c in st.campaigns},
            "selected_count": len(await store.selected_ids()),
            "total_points": sum(c.balance or 0 for c in channels),
            "week_total": sum(week.get(c.channel.channel_id, 0) for c in channels),
            "drops_claimed": await store.count_events("drop"),
            "slot_count": farmer.slots,
        }

    # --- pages -----------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return render(request, "dashboard.html", **await dashboard_ctx())

    @app.get("/partials/dashboard", response_class=HTMLResponse)
    async def dashboard_partial(request: Request):
        return render(request, "_dashboard.html", partial=True, **await dashboard_ctx())

    @app.get("/campaigns", response_class=HTMLResponse)
    async def campaigns_page(request: Request):
        now = farmer.clock()
        states = await store.campaign_states()
        drop_slot = farmer.state.plan.drop_slot
        farming_id = drop_slot.reason.campaign_id if drop_slot else None
        by_game: dict[str, list[Campaign]] = defaultdict(list)
        for c in farmer.state.campaigns:
            if c.ended(now) and not c.claimable_drops(now):
                continue
            by_game[c.game.name].append(c)
        games = [
            (name, sorted(cs, key=lambda c: c.ends_at))
            for name, cs in sorted(by_game.items(), key=lambda kv: kv[0].lower())
        ]
        resp = render(
            request, "campaigns.html", games=games, states=states, now=now, farming_id=farming_id
        )
        await store.mark_all_seen()  # the "new" badge shows once
        return resp

    @app.post("/campaigns/{campaign_id}/select")
    async def select_campaign(request: Request, campaign_id: str, selected: str = Form("")):
        await store.set_selected(campaign_id, selected == "1")
        farmer.wake()
        campaign = next((c for c in farmer.state.campaigns if c.id == campaign_id), None)
        if request.headers.get("HX-Request") and campaign is not None:
            # Swap just this card, so the page doesn't reload and jump to the top.
            drop_slot = farmer.state.plan.drop_slot
            return render(
                request,
                "_campaign.html",
                c=campaign,
                states=await store.campaign_states(),
                now=farmer.clock(),
                farming_id=drop_slot.reason.campaign_id if drop_slot else None,
            )
        return back("/campaigns")

    @app.post("/campaigns/refresh")
    async def refresh_campaigns():
        farmer.wake(refresh_campaigns=True)
        return back("/campaigns")

    def channel_login(raw: str) -> str:
        """Accept "name", "@name" or a twitch.tv URL."""
        return raw.strip().rstrip("/").lstrip("@").rsplit("/", 1)[-1].lower()

    def competing_drops() -> dict[str, str]:
        """Live channels left out because their Drops would take progress from the farmed one,
        mapped to the name of the campaign being farmed."""
        st = farmer.state
        slot = st.plan.drop_slot
        if slot is None or not isinstance(slot.reason, DropReason):
            return {}
        c = next((c for c in st.campaigns if c.id == slot.reason.campaign_id), None)
        if c is None:
            return {}
        return {cid: c.name for cid, s in st.live.items() if competes(s, c)}

    @app.get("/channels", response_class=HTMLResponse)
    async def channels_page(request: Request, error: str = "", check: str = ""):
        checked = None
        if check and farmer.state.account:
            login = channel_login(check)
            try:
                found = await farmer.check_balance(login)
            except TwitchError as e:
                error = f"Balance check failed: {e}"
            else:
                checked = {
                    "login": login,
                    "user": found[0] if found else None,
                    "points": found[1] if found else None,
                }
        channels = await store.points_channel_info()
        return render(
            request,
            "channels.html",
            channels=channels,
            total=sum(c.balance or 0 for c in channels),
            live=farmer.state.live,
            error=error,
            check=check,
            checked=checked,
            on_list={c.channel.channel_id for c in channels},
            watched={s.stream.channel_id for s in farmer.state.plan.slots},
            skipped=farmer.state.skipped,
            competing=competing_drops(),
        )

    @app.post("/channels/balances")
    async def refresh_balances():
        if farmer.state.account is None:
            return back("/channels", "Log in first.")
        try:
            await farmer.refresh_balances()
        except TwitchError as e:
            return back("/channels", f"Balance refresh failed: {e}")
        return back("/channels")

    @app.post("/channels/add")
    async def add_channel(login: str = Form(...)):
        if farmer.state.account is None:
            return back("/login", "Log in first.")
        login = channel_login(login)
        if not login:
            return back("/channels")
        try:
            user = await farmer.twitch.lookup_user(login)
        except TwitchError as e:
            return back("/channels", f"Lookup failed: {e}")
        if user is None:
            return back("/channels", f"No Twitch channel named {login}")
        await store.add_points_channel(user.channel_id, user.login, user.display_name)
        farmer.wake()
        return back("/channels")

    @app.post("/channels/import")
    async def import_follows():
        if farmer.state.account is None:
            return back("/login", "Log in first.")
        try:
            follows = await farmer.twitch.followed_channels()
        except TwitchError as e:
            return back("/channels", f"Import failed: {e}")
        for u in follows:
            if not u.channel_id:
                found = await farmer.twitch.lookup_user(u.login)
                if found is None:
                    continue
                u = found
            await store.add_points_channel(u.channel_id, u.login, u.display_name)
        farmer.wake()
        return back("/channels")

    @app.post("/channels/{channel_id}/remove")
    async def remove_channel(channel_id: str):
        await store.remove_points_channel(channel_id)
        farmer.wake()
        return back("/channels")

    @app.post("/channels/{channel_id}/move")
    async def move_channel(channel_id: str, delta: int = Form(...)):
        await store.move_points_channel(channel_id, delta)
        farmer.wake()
        return back("/channels")

    @app.post("/channels/order")
    async def order_channels(order: str = Form("")):
        """Set the whole ranking at once (drag and drop). `order` is comma-separated channel ids."""
        await store.reorder_points_channels([c for c in order.split(",") if c])
        farmer.wake()
        return Response(status_code=204)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: str = ""):
        return render(request, "login.html", login_url=LOGIN_URL, error=error)

    @app.post("/login")
    async def login(pasted: str = Form(...)):
        try:
            await farmer.login(pasted)
        except AuthError as e:
            return back("/login", e)
        except TwitchError as e:
            return back("/login", f"Could not reach Twitch: {e}")
        return back("/")

    @app.post("/logout")
    async def logout():
        await farmer.logout()
        return back("/login")

    @app.post("/resume")
    async def resume():
        farmer.resume()
        return back("/")

    @app.get("/healthz")
    async def healthz():
        st = farmer.state
        return JSONResponse(
            {
                "status": st.status,
                "last_tick": st.last_tick.isoformat() if st.last_tick else None,
                "version": __version__,
                "revision": REVISION or None,
            }
        )

    return app
