"""Entry point: `python -m dropbot` (or the `dropbot` script).

Configuration is by environment variable only:
  DATABASE_URL  Postgres URL; unset means SQLite at /data/bot.db
  PORT          web UI port (default 8080)
  HOST          bind address (default 0.0.0.0)
  LOG_LEVEL     DEBUG / INFO / WARNING (default INFO)
  POINTS_DURING_DROPS  1 to also watch Points Channels while a Drop is farmed (default 0)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import uvicorn

from . import version_label
from .farmer import Farmer
from .store import Store
from .twitch.client import TwitchClient
from .web.app import create_app


def build_app():
    store = Store(os.environ.get("DATABASE_URL"))
    twitch = TwitchClient()
    points_during_drops = os.environ.get("POINTS_DURING_DROPS", "").lower() in ("1", "true", "yes")
    farmer = Farmer(store, twitch, points_during_drops=points_during_drops)

    @asynccontextmanager
    async def lifespan(_app):
        logging.getLogger(__name__).info("database: %s", store.dialect)
        await store.migrate()
        await farmer.start()
        try:
            yield
        finally:
            await farmer.stop()
            await twitch.close()
            await store.close()

    return create_app(farmer, lifespan=lifespan)


def main() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Twitch request logs are noisy at INFO.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger(__name__).info("dropbot %s", version_label())
    uvicorn.run(
        build_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
