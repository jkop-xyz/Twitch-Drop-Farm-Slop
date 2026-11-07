from datetime import UTC, datetime, timedelta

from dropbot.store import Account, normalize_url

from .factories import campaign


def test_database_url_normalization():
    assert normalize_url(None) == "sqlite+aiosqlite:////data/bot.db"
    assert normalize_url("") == "sqlite+aiosqlite:////data/bot.db"
    assert normalize_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_url("sqlite:////x/y.db") == "sqlite+aiosqlite:////x/y.db"


async def test_migrate_is_idempotent(store):
    await store.migrate()


async def test_account_roundtrip_and_logout(store):
    assert await store.get_account() is None
    await store.save_account(Account("tok", "42", "me", "Me", "dev"))
    await store.save_account(Account("tok2", "42", "me", "Me", "dev"))
    assert (await store.get_account()).access_token == "tok2"
    await store.clear_account()
    assert await store.get_account() is None


async def test_new_campaigns_start_unselected_and_unseen(store):
    await store.observe_campaigns([campaign("c1"), campaign("c2")])
    states = await store.campaign_states()
    assert {cid: (s.selected, s.seen) for cid, s in states.items()} == {
        "c1": (False, False),
        "c2": (False, False),
    }
    assert await store.selected_ids() == frozenset()


async def test_selecting_marks_seen_and_survives_reobservation(store):
    await store.observe_campaigns([campaign("c1")])
    await store.set_selected("c1", True)
    await store.observe_campaigns([campaign("c1"), campaign("c3")])
    states = await store.campaign_states()
    assert states["c1"].selected and states["c1"].seen
    assert not states["c3"].seen
    await store.deselect(["c1"])
    assert await store.selected_ids() == frozenset()


async def test_mark_all_seen(store):
    await store.observe_campaigns([campaign("c1")])
    await store.mark_all_seen()
    assert (await store.campaign_states())["c1"].seen


async def test_points_channels_ranking(store):
    assert await store.add_points_channel("1", "a")
    assert await store.add_points_channel("2", "b")
    assert await store.add_points_channel("3", "c")
    assert not await store.add_points_channel("2", "b")
    await store.move_points_channel("3", -1)
    assert [p.login for p in await store.points_channels()] == ["a", "c", "b"]
    await store.reorder_points_channels(["2"])
    assert [p.login for p in await store.points_channels()] == ["b", "a", "c"]
    await store.remove_points_channel("1")
    assert [p.login for p in await store.points_channels()] == ["b", "c"]
    assert [p.rank for p in await store.points_channels()] == [0, 2]


async def test_balance_tracking(store):
    await store.add_points_channel("1", "a")
    assert await store.set_balance("1", 100) is None
    assert await store.set_balance("1", 160) == 100
    info = (await store.points_channel_info())[0]
    assert info.balance == 160 and info.balance_at.tzinfo is not None


async def test_points_enabled_is_unknown_until_set(store):
    await store.add_points_channel("1", "a")
    assert (await store.points_channels())[0].points_enabled is None
    await store.set_points_enabled("1", False)
    assert (await store.points_channels())[0].points_off
    await store.set_points_enabled("1", True)
    assert (await store.points_channels())[0].points_enabled is True


async def test_claim_history_and_points_summary(store):
    now = datetime.now(UTC)
    await store.record("points", amount=10, channel_id="1", at=now - timedelta(days=10))
    await store.record("points", amount=50, channel_id="1", at=now - timedelta(hours=1))
    await store.record("points", amount=7, channel_id="2")
    await store.record("bonus", channel_id="1")
    await store.record("drop", campaign_id="c1", detail="Cool Hat")
    assert await store.points_gained_since(now - timedelta(days=7)) == {"1": 50, "2": 7}
    recent = await store.recent_events(limit=10)
    assert recent[0].kind == "drop" and recent[0].at.tzinfo is not None
    assert [e.kind for e in await store.recent_events(kinds=["drop"])] == ["drop"]


# --- per-account data --------------------------------------------------------------------


async def test_data_is_scoped_per_account(store):
    from dropbot.store import NoAccount

    await store.add_points_channel("1", "a")
    await store.observe_campaigns([campaign("c1")])
    await store.set_selected("c1", True)
    await store.record("drop", campaign_id="c1")

    store.use_account("u2")
    assert await store.points_channels() == []
    assert await store.selected_ids() == frozenset()
    assert await store.count_events("drop") == 0
    assert await store.add_points_channel("1", "a")  # same channel, other account: allowed
    await store.observe_campaigns([campaign("c1")])
    assert not (await store.campaign_states())["c1"].selected

    store.use_account(None)
    assert await store.points_channel_info() == []
    assert await store.campaign_states() == {}
    assert await store.recent_events() == []
    assert await store.points_gained_since(datetime.now(UTC) - timedelta(days=1)) == {}
    import pytest

    with pytest.raises(NoAccount):
        await store.add_points_channel("9", "z")

    store.use_account("u1")
    assert [p.login for p in await store.points_channels()] == ["a"]
    assert await store.selected_ids() == {"c1"}
    assert await store.count_events("drop") == 1


async def test_migration_assigns_existing_data_to_stored_account(tmp_path):
    """A database from before per-account data keeps everything for the logged-in account."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from dropbot.store import Store

    s = Store(f"sqlite:///{tmp_path}/old.db")
    cfg = Config()
    cfg.set_main_option("script_location", "dropbot:migrations")

    def to_0001(conn):
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")

    async with s.engine.begin() as conn:
        await conn.run_sync(to_0001)
        now = "2026-11-01 00:00:00"
        await conn.execute(
            text(
                "INSERT INTO account (id, access_token, user_id, login, display_name, device_id, updated_at)"
                f" VALUES (1, 'tok', '777', 'me', 'Me', 'dev', '{now}')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO points_channel (channel_id, login, display_name, rank, balance)"
                " VALUES ('5', 'streamer', 'Streamer', 0, 1234)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO campaign_state (campaign_id, name, game_name, first_seen_at, seen, selected)"
                f" VALUES ('c1', 'C', 'G', '{now}', 1, 1)"
            )
        )
        await conn.execute(
            text(
                f"INSERT INTO event (at, kind, channel_id, channel_name, amount, detail) VALUES ('{now}', 'drop', NULL, '', 0, 'Hat')"
            )
        )

    await s.migrate()
    s.use_account("777")
    [info] = await s.points_channel_info()
    assert (info.channel.login, info.balance) == ("streamer", 1234)
    assert await s.selected_ids() == {"c1"}
    assert await s.count_events("drop") == 1
    s.use_account("other")
    assert await s.points_channels() == [] and await s.count_events("drop") == 0
    await s.close()
