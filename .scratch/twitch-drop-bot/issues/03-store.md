# 03 Store

Status: done
Blocked by: 01

SQLAlchemy 2 async models and Alembic migrations for the account, campaign selection and seen-state, Points Channels with rank, and claim history. `DATABASE_URL` with SQLite fallback. Migrations run on startup.

Done when Store tests pass on SQLite (and on Postgres when TEST_DATABASE_URL is set).
