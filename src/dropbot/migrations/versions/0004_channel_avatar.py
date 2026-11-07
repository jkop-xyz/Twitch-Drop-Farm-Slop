"""store each Points Channel's profile picture

Revision ID: 0004
Revises: 0003

points_enabled is reset so every channel is looked up again on the next tick, which also
fills in the new avatar_url (both come from the same request).
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("points_channel", sa.Column("avatar_url", sa.String(300), nullable=True))
    op.execute("UPDATE points_channel SET points_enabled = NULL")


def downgrade() -> None:
    with op.batch_alter_table("points_channel") as batch:
        batch.drop_column("avatar_url")
