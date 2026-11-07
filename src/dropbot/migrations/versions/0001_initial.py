"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("access_token", sa.String(200), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("login", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("device_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("updated_at", TZ, nullable=False),
    )
    op.create_table(
        "campaign_state",
        sa.Column("campaign_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False, server_default=""),
        sa.Column("game_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("ends_at", TZ, nullable=True),
        sa.Column("first_seen_at", TZ, nullable=False),
        sa.Column("seen", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("selected", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "points_channel",
        sa.Column("channel_id", sa.String(32), primary_key=True),
        sa.Column("login", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("balance", sa.BigInteger, nullable=True),
        sa.Column("balance_at", TZ, nullable=True),
    )
    op.create_table(
        "event",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("at", TZ, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("channel_id", sa.String(32), nullable=True),
        sa.Column("channel_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("campaign_id", sa.String(64), nullable=True),
        sa.Column("amount", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_event_at", "event", ["at"])
    op.create_index("ix_event_kind", "event", ["kind"])
    op.create_index("ix_event_channel_id", "event", ["channel_id"])


def downgrade() -> None:
    op.drop_table("event")
    op.drop_table("points_channel")
    op.drop_table("campaign_state")
    op.drop_table("account")
