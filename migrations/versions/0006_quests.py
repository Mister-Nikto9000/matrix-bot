"""quests: ежедневные квесты

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("goal", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("rewarded", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("user_id", "day", name="uq_quests_user_day"),
    )
    op.create_index("ix_quests_user_id", "quests", ["user_id"])
    op.create_index("ix_quests_day", "quests", ["day"])


def downgrade() -> None:
    op.drop_index("ix_quests_day", table_name="quests")
    op.drop_index("ix_quests_user_id", table_name="quests")
    op.drop_table("quests")
