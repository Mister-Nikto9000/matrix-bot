"""metrics: users.last_seen_at/source/notify_*, daily_activity, errors, bot_state

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("source", sa.String(32), nullable=True))
        batch.add_column(sa.Column("notify_daily", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("notify_marketing", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("notify_hour", sa.Integer(), nullable=False, server_default="9"))
    # Уже существующим пользователям источник известен только по факту приглашения
    op.execute("UPDATE users SET source = 'ref' WHERE referrer_id IS NOT NULL")
    op.execute("UPDATE users SET source = 'organic' WHERE referrer_id IS NULL")

    op.create_table(
        "daily_activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.UniqueConstraint("user_id", "day", name="uq_daily_activity_user_day"),
    )
    op.create_index("ix_daily_activity_user_id", "daily_activity", ["user_id"])
    op.create_index("ix_daily_activity_day", "daily_activity", ["day"])

    op.create_table(
        "errors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("message", sa.Text()),
    )
    op.create_index("ix_errors_created_at", "errors", ["created_at"])

    op.create_table(
        "bot_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bot_state")
    op.drop_index("ix_errors_created_at", table_name="errors")
    op.drop_table("errors")
    op.drop_index("ix_daily_activity_day", table_name="daily_activity")
    op.drop_index("ix_daily_activity_user_id", table_name="daily_activity")
    op.drop_table("daily_activity")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("notify_hour")
        batch.drop_column("notify_marketing")
        batch.drop_column("notify_daily")
        batch.drop_column("source")
        batch.drop_column("last_seen_at")
