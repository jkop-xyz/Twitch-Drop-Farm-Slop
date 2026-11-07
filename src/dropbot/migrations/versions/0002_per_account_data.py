"""scope campaign selections, points channels and history to a Twitch account

Revision ID: 0002
Revises: 0001

Existing rows are assigned to the currently stored account (or '' if logged out, which
leaves them hidden until an account with that id exists; in practice there always was one).
campaign_state and points_channel get composite primary keys, which SQLite can't ALTER, so
they are rebuilt: create new table, copy, drop old, rename.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def _owner() -> str:
    row = op.get_bind().execute(sa.text("SELECT user_id FROM account LIMIT 1")).first()
    return row[0] if row else ""


def upgrade() -> None:
    owner = _owner()

    op.create_table(
        "campaign_state_new",
        sa.Column("user_id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False, server_default=""),
        sa.Column("game_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("ends_at", TZ, nullable=True),
        sa.Column("first_seen_at", TZ, nullable=False),
        sa.Column("seen", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("selected", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            "INSERT INTO campaign_state_new (user_id, campaign_id, name, game_name, ends_at,"
            " first_seen_at, seen, selected) SELECT :owner, campaign_id, name, game_name,"
            " ends_at, first_seen_at, seen, selected FROM campaign_state"
        ).bindparams(owner=owner)
    )
    op.drop_table("campaign_state")
    op.rename_table("campaign_state_new", "campaign_state")

    op.create_table(
        "points_channel_new",
        sa.Column("user_id", sa.String(32), primary_key=True),
        sa.Column("channel_id", sa.String(32), primary_key=True),
        sa.Column("login", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("balance", sa.BigInteger, nullable=True),
        sa.Column("balance_at", TZ, nullable=True),
    )
    op.execute(
        sa.text(
            "INSERT INTO points_channel_new (user_id, channel_id, login, display_name, rank,"
            " balance, balance_at) SELECT :owner, channel_id, login, display_name, rank,"
            " balance, balance_at FROM points_channel"
        ).bindparams(owner=owner)
    )
    op.drop_table("points_channel")
    op.rename_table("points_channel_new", "points_channel")

    with op.batch_alter_table("event") as batch:
        batch.add_column(sa.Column("user_id", sa.String(32), nullable=False, server_default=""))
    op.execute(sa.text("UPDATE event SET user_id = :owner").bindparams(owner=owner))
    op.create_index("ix_event_user_id", "event", ["user_id"])


def downgrade() -> None:
    raise NotImplementedError("per-account data can't be merged back into a single account")
