"""remember whether each Points Channel has Channel Points turned on

Revision ID: 0003
Revises: 0002

NULL means not checked yet; the bot checks such channels before it next plans.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("points_channel", sa.Column("points_enabled", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("points_channel") as batch:
        batch.drop_column("points_enabled")
