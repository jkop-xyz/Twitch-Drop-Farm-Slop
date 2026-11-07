"""The farming loop: refresh state from Twitch, ask the Scheduler, apply the Plan, record it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from .domain import Campaign, DropReason, Plan, PointsChannel, SlotAssignment, Stream
from .scheduler import WATCH_SLOTS, Snapshot, farmable_campaigns, plan
from .store import Account, Store
from .twitch import AuthError, PointsState, ProtocolError, TransientError, TwitchApi, UserInfo
from .twitch.parse import extract_token
from .twitch.protocol import SPADE_INTERVAL_S, WATCH_INTERVAL_S

log = logging.getLogger(__name__)

TICK = timedelta(seconds=60)
CAMPAIGN_REFRESH = timedelta(minutes=15)
# Balances of Points Channels that aren't being watched are refreshed this often.
BALANCE_REFRESH = timedelta(minutes=30)
# A stream that fails to watch this many times in a row is skipped for SKIP_FOR
# (sub-only, geo-blocked, broken playlist...), so the Scheduler picks another one.
WATCH_FAIL_LIMIT = 3
SKIP_FOR = timedelta(minutes=10)
# Cap on allowed-channel lookups per campaign per tick.
MAX_ALLOWED_LOOKUPS = 100


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class FarmerState:
    """Everything the web UI shows. Replaced or mutated only by the Farmer."""

    status: str = "starting"  # starting | logged_out | running | paused
    account: Account | None = None
    login_message: str = ""
    protocol_error: str | None = None
    protocol_error_at: datetime | None = None
    transient_error: str | None = None
    watch_report_error: str | None = None
    # Channels that kept failing to watch, skipped until the given time.
    skipped: dict[str, datetime] = field(default_factory=dict)
    # Channel Points balance on each currently watched stream, by channel id.
    slot_balances: dict[str, int] = field(default_factory=dict)
    # Watched channels (by id) that have Channel Points turned off.
    slot_points_off: set[str] = field(default_factory=set)
    # Profile pictures of watched channels, by channel id (Points Channels' are also stored).
    avatars: dict[str, str] = field(default_factory=dict)
    balances_at: datetime | None = None
    campaigns: list[Campaign] = field(default_factory=list)
    campaigns_at: datetime | None = None
    live: dict[str, Stream] = field(default_factory=dict)
    plan: Plan = field(default_factory=Plan)
    last_tick: datetime | None = None


class Farmer:
    def __init__(
        self,
        store: Store,
        twitch: TwitchApi,
        *,
        clock: Callable[[], datetime] = utcnow,
        slots: int = WATCH_SLOTS,
    ) -> None:
        self.store = store
        self.twitch = twitch
        self.clock = clock
        self.slots = slots
        self.state = FarmerState()
        self._wake = asyncio.Event()
        self._force_campaigns = True
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._watch_failures: dict[str, int] = {}
        # Points Channels whose Channel Points state was looked up this session (see _tick).
        self._points_checked: set[str] = set()

    # --- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        """Restore the stored login, then run the loops in the background."""
        await self.restore_login()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="tick"),
            asyncio.create_task(self._watch_loop(), name="watch"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks = []

    def wake(self, *, refresh_campaigns: bool = False) -> None:
        """Re-plan soon, e.g. after the owner changed a selection."""
        if refresh_campaigns:
            self._force_campaigns = True
        self._wake.set()

    @property
    def farming(self) -> bool:
        return self.state.status == "running"

    # --- login -----------------------------------------------------------------------------

    async def restore_login(self) -> None:
        account = await self.store.get_account()
        if account is None:
            self._logged_out("")
            return
        try:
            await self.twitch.validate(account.access_token)
        except AuthError as e:
            await self.store.clear_account()
            self._logged_out(f"Stored login no longer works ({e}). Please log in again.")
            return
        except TransientError as e:
            # Can't reach Twitch right now: trust the stored token, the tick will tell.
            log.warning("could not validate stored token: %s", e)
        self._logged_in(account)

    async def login(self, pasted: str) -> Account:
        token = extract_token(pasted)
        if token is None:
            raise AuthError("No access token found in what you pasted.")
        info = await self.twitch.validate(token)
        await self.store.adopt_unowned(info.user_id)
        existing = await self.store.get_account()
        device_id = existing.device_id if existing and existing.device_id else ""
        if not device_id:
            device_id = await self.twitch.new_device_id()
        account = Account(token, info.user_id, info.login, info.login, device_id)
        await self.store.save_account(account)
        self._logged_in(account)
        self.wake(refresh_campaigns=True)
        return account

    async def logout(self) -> None:
        await self.store.clear_account()
        self._logged_out("Logged out.")

    def _logged_in(self, account: Account) -> None:
        if self.store.account_id != account.user_id:
            self._forget_account_state()  # switching accounts: nothing carries over
        self.store.use_account(account.user_id)
        self.twitch.authenticate(account.access_token, account.user_id, account.device_id)
        self.state.account = account
        self.state.login_message = ""
        self.state.status = "paused" if self.state.protocol_error else "running"
        self._force_campaigns = True
        log.info("logged in as %s", account.login)

    def _logged_out(self, message: str) -> None:
        self.twitch.deauthenticate()
        # The account's data stays in the database (it comes back on the next login as the
        # same account), but nothing of it is shown or used while logged out.
        self.store.use_account(None)
        self._forget_account_state()
        self.state.account = None
        self.state.status = "logged_out"
        self.state.login_message = message

    def _forget_account_state(self) -> None:
        st = self.state
        st.plan = Plan()
        st.campaigns = []
        st.campaigns_at = None
        st.live = {}
        st.slot_balances = {}
        st.slot_points_off = set()
        st.avatars = {}
        st.skipped = {}
        st.balances_at = None
        st.transient_error = None
        st.watch_report_error = None
        self._watch_failures = {}
        self._points_checked = set()
        self._force_campaigns = True

    def resume(self) -> None:
        """Clear a protocol error after the owner has updated the code or wants to retry."""
        self.state.protocol_error = None
        self.state.protocol_error_at = None
        if self.state.account is not None:
            self.state.status = "running"
        self.wake(refresh_campaigns=True)

    # --- loops -----------------------------------------------------------------------------

    async def _tick_loop(self) -> None:
        while True:
            if self.farming:
                await self.tick()
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK.total_seconds())

    async def _watch_loop(self) -> None:
        last_spade: dict[str, float] = {}
        loop = asyncio.get_running_loop()
        while True:
            try:
                if self.farming:
                    now = loop.time()
                    for slot in self.state.plan.slots:
                        s = slot.stream
                        ok = await self._guard(self.twitch.watch(s))
                        if ok is not None:
                            self._record_watch(s, ok)
                        if now - last_spade.get(s.broadcast_id, 0) >= SPADE_INTERVAL_S:
                            if await self._send_minute_watched(s):
                                last_spade[s.broadcast_id] = now
                    # Forget broadcasts we no longer watch.
                    current = {sl.stream.broadcast_id for sl in self.state.plan.slots}
                    last_spade = {k: v for k, v in last_spade.items() if k in current}
            except Exception as e:  # never let the watch loop die
                log.exception("watch loop error")
                self.state.transient_error = f"Unexpected error while watching: {e!r}"
            await asyncio.sleep(WATCH_INTERVAL_S)

    def _record_watch(self, stream: Stream, ok: bool) -> None:
        cid = stream.channel_id
        if ok:
            self._watch_failures.pop(cid, None)
            return
        n = self._watch_failures.get(cid, 0) + 1
        self._watch_failures[cid] = n
        if n >= WATCH_FAIL_LIMIT:
            self._watch_failures.pop(cid, None)
            self.state.skipped[cid] = self.clock() + SKIP_FOR
            log.warning("can't watch %s, skipping it for %s", stream.name, SKIP_FOR)
            self.wake()

    async def _send_minute_watched(self, stream: Stream) -> bool:
        """Minute-watched reports fail on their own (e.g. a DNS blocker on spade.twitch.tv).
        They get a separate notice and never count as a failed refresh."""
        try:
            ok = await self.twitch.send_minute_watched(stream)
        except TransientError as e:
            if self.state.watch_report_error != str(e):
                log.warning("minute-watched report failed: %s", e)
            self.state.watch_report_error = str(e)
            return False
        except (ProtocolError, AuthError):
            return False
        if ok and self.state.watch_report_error:
            log.info("minute-watched reports working again")
            self.state.watch_report_error = None
        return ok

    async def _guard(self, coro):
        """Run a Twitch call, turning failures into Farmer state instead of exceptions."""
        try:
            return await coro
        except ProtocolError as e:
            self._pause(e)
        except AuthError as e:
            await self.store.clear_account()
            self._logged_out(f"Twitch rejected the login ({e}). Please log in again.")
        except TransientError as e:
            self.state.transient_error = str(e)
            log.warning("transient: %s", e)
        return None

    def _pause(self, e: ProtocolError) -> None:
        log.error("Twitch protocol error, pausing: %s", e)
        self.state.status = "paused"
        self.state.protocol_error = str(e)
        self.state.protocol_error_at = self.clock()
        self.state.plan = Plan()

    # --- one tick --------------------------------------------------------------------------

    async def tick(self) -> None:
        async with self._lock:
            try:
                await self._tick()
                self.state.transient_error = None
            except ProtocolError as e:
                self._pause(e)
            except AuthError as e:
                await self.store.clear_account()
                self._logged_out(f"Twitch rejected the login ({e}). Please log in again.")
            except TransientError as e:
                self.state.transient_error = str(e)
                log.warning("tick failed, retrying next tick: %s", e)
            except Exception as e:  # e.g. the database went away; never let the loop die
                log.exception("unexpected error during tick")
                self.state.transient_error = f"Unexpected error: {e!r}"

    async def _tick(self) -> None:
        now = self.clock()
        st = self.state
        if (
            self._force_campaigns
            or st.campaigns_at is None
            or now - st.campaigns_at >= CAMPAIGN_REFRESH
        ):
            st.campaigns = await self.twitch.campaigns()
            st.campaigns_at = now
            self._force_campaigns = False
            await self.store.observe_campaigns(st.campaigns)

        selected = await self.store.selected_ids()
        points_channels = await self.store.points_channels()
        # Channels not checked yet (new, or from before this was tracked): find out whether they
        # have Channel Points before planning, so one that doesn't never gets a points slot.
        unchecked = [
            p
            for p in points_channels
            if p.points_enabled is None and p.channel_id not in self._points_checked
        ]
        if unchecked:
            self._points_checked.update(p.channel_id for p in unchecked)
            await self._refresh_balances(unchecked)
            points_channels = await self.store.points_channels()

        # Which streams could matter: Points Channels, plus candidates for Farmable Campaigns.
        probe = Snapshot(now, st.campaigns, selected, {}, points_channels, slots=self.slots)
        farmable = farmable_campaigns(probe)
        logins = [p.login for p in points_channels]
        for c in farmable:
            logins += list(c.allowed_logins[:MAX_ALLOWED_LOOKUPS])
        live = await self.twitch.live_streams(logins)
        # The game directory also covers campaigns whose allow list is longer than we look up;
        # the Scheduler only accepts streams the campaign allows.
        for game in {c.game for c in farmable}:
            for s in await self.twitch.game_streams(game):
                live.setdefault(s.channel_id, s)
        st.live = live

        st.skipped = {cid: until for cid, until in st.skipped.items() if until > now}
        watchable = {cid: s for cid, s in live.items() if cid not in st.skipped}

        current = st.plan.drop_slot.stream.channel_id if st.plan.drop_slot else None
        new_plan = plan(
            Snapshot(now, st.campaigns, selected, watchable, points_channels, current, self.slots)
        )

        if new_plan.drop_slot:
            await self._update_drop_progress(new_plan.drop_slot)
            # Progress may have finished a Drop: plan again so it gets claimed this tick.
            new_plan = plan(
                Snapshot(
                    now, st.campaigns, selected, watchable, points_channels, current, self.slots
                )
            )

        claimed_any = False
        for campaign_id, drop in new_plan.claims:
            if await self.twitch.claim_drop(campaign_id, drop.id):
                self._mark_claimed(campaign_id, drop.id)
                slot = new_plan.drop_slot
                await self.store.record(
                    "drop",
                    campaign_id=campaign_id,
                    detail=", ".join(drop.rewards) or drop.name,
                    channel_id=slot.stream.channel_id if slot else None,
                    channel_name=slot.stream.name if slot else "",
                )
                log.info("claimed drop %s (%s)", drop.name, ", ".join(drop.rewards))
                self._force_campaigns = True
                claimed_any = True
        if claimed_any:
            # Claims can finish a campaign or unlock the next Drop: plan once more.
            new_plan = plan(
                Snapshot(
                    now, st.campaigns, selected, watchable, points_channels, current, self.slots
                )
            )

        if new_plan.deselect:
            await self.store.deselect(new_plan.deselect)

        self._log_changes(st.plan, new_plan)
        st.plan = new_plan
        st.last_tick = now

        points_ids = {p.channel_id for p in points_channels}
        st.slot_balances = {
            s.stream.channel_id: st.slot_balances[s.stream.channel_id]
            for s in new_plan.slots
            if s.stream.channel_id in st.slot_balances
        }
        st.slot_points_off = set()
        st.avatars = {}
        for slot in new_plan.slots:
            if slot.stream.channel_id in points_ids:
                await self._farm_points(slot.stream)
            elif (pts := await self.twitch.points(slot.stream.login)) is not None:
                # A Drop-only stream: show the balance, but don't claim or record anything.
                if pts.avatar_url:
                    st.avatars[slot.stream.channel_id] = pts.avatar_url
                if pts.enabled:
                    st.slot_balances[slot.stream.channel_id] = pts.balance
                else:
                    st.slot_points_off.add(slot.stream.channel_id)

        if st.balances_at is None or now - st.balances_at >= BALANCE_REFRESH:
            watched = {s.stream.channel_id for s in new_plan.slots}
            await self._refresh_balances(
                [p for p in points_channels if p.channel_id not in watched]
            )
            st.balances_at = now

    async def _refresh_balances(self, channels: list[PointsChannel]) -> int:
        """Store current balances without recording gains (the bot didn't earn these)."""
        if not channels:
            return 0
        states = await self.twitch.points_many([p.login for p in channels])
        n = 0
        for p in channels:
            if (state := states.get(p.login)) is not None:
                await self.store.set_points_enabled(p.channel_id, state.enabled, state.avatar_url)
                if state.enabled:
                    await self.store.set_balance(p.channel_id, state.balance)
                n += 1
        return n

    async def refresh_balances(self) -> int:
        """Fetch the balance of every Points Channel now (from the web UI)."""
        async with self._lock:
            channels = await self.store.points_channels()
            n = await self._refresh_balances(channels)
            self.state.balances_at = self.clock()
            return n

    async def check_balance(self, login: str) -> tuple[UserInfo, PointsState | None] | None:
        """Look up the Account's balance on any channel, on the list or not."""
        user = await self.twitch.lookup_user(login)
        if user is None:
            return None
        return user, await self.twitch.points(user.login)

    async def _update_drop_progress(self, slot: SlotAssignment) -> None:
        assert isinstance(slot.reason, DropReason)
        session = await self.twitch.current_drop(slot.stream.channel_id)
        if session is None:
            return
        st = self.state
        for i, c in enumerate(st.campaigns):
            if c.id != slot.reason.campaign_id:
                continue
            drops = tuple(
                replace(d, current_minutes=max(d.current_minutes, session.current_minutes))
                if d.id == session.drop_id
                else d
                for d in c.drops
            )
            st.campaigns[i] = replace(c, drops=drops)

    def _mark_claimed(self, campaign_id: str, drop_id: str) -> None:
        st = self.state
        for i, c in enumerate(st.campaigns):
            if c.id == campaign_id:
                drops = tuple(replace(d, claimed=True) if d.id == drop_id else d for d in c.drops)
                st.campaigns[i] = replace(c, drops=drops)

    async def _farm_points(self, stream: Stream) -> None:
        state = await self.twitch.points(stream.login)
        if state is None:
            return
        await self.store.set_points_enabled(stream.channel_id, state.enabled, state.avatar_url)
        if state.avatar_url:
            self.state.avatars[stream.channel_id] = state.avatar_url
        if not state.enabled:
            # Turned off since the last check. Planning leaves it out from the next tick on.
            self.state.slot_points_off.add(stream.channel_id)
            return
        if state.bonus_claim_id and await self.twitch.claim_bonus(
            stream.channel_id, state.bonus_claim_id
        ):
            await self.store.record("bonus", channel_id=stream.channel_id, channel_name=stream.name)
            log.info("claimed Bonus Chest on %s", stream.name)
        self.state.slot_balances[stream.channel_id] = state.balance
        previous = await self.store.set_balance(stream.channel_id, state.balance)
        if previous is not None and state.balance > previous:
            log.info("+%d Channel Points on %s", state.balance - previous, stream.name)
            await self.store.record(
                "points",
                amount=state.balance - previous,
                channel_id=stream.channel_id,
                channel_name=stream.name,
            )

    @staticmethod
    def _log_changes(old: Plan, new: Plan) -> None:
        before = {s.stream.channel_id for s in old.slots}
        after = {s.stream.channel_id for s in new.slots}
        if before != after:
            names = ", ".join(s.stream.name for s in new.slots) or "nothing"
            log.info("now watching: %s", names)
