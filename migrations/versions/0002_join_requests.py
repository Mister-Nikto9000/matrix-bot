"""join requests: channels.join_mode, channels.auto_approve, join_requests

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("channels") as batch:
        batch.add_column(sa.Column("join_mode", sa.String(16), nullable=False, server_default="public"))
        batch.add_column(sa.Column("auto_approve", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "join_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tg_id", "chat_id", name="uq_join_requests_tg_chat"),
    )
    op.create_index("ix_join_requests_chat_id", "join_requests", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_join_requests_chat_id", table_name="join_requests")
    op.drop_table("join_requests")
    with op.batch_alter_table("channels") as batch:
        batch.drop_column("auto_approve")
        batch.drop_column("join_mode")
