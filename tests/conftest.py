import os

import pytest

from dropbot.store import Store

BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("TEST_DATABASE_URL") else [])


@pytest.fixture(params=BACKENDS)
async def store(request, tmp_path):
    if request.param == "sqlite":
        s = Store(f"sqlite:///{tmp_path}/test.db")
    else:
        s = Store(os.environ["TEST_DATABASE_URL"])
        # Start from a clean schema on the shared test database.
        from sqlalchemy import text

        async with s.engine.begin() as conn:
            for t in ("event", "points_channel", "campaign_state", "account", "alembic_version"):
                await conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
    await s.migrate()
    yield s
    await s.close()
