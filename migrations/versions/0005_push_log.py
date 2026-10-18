"""push_log: журнал автопушей

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clicked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "kind", name="uq_push_log_user_kind"),
    )
    op.create_index("ix_push_log_user_id", "push_log", ["user_id"])
    op.create_index("ix_push_log_sent_at", "push_log", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_push_log_sent_at", table_name="push_log")
    op.drop_index("ix_push_log_user_id", table_name="push_log")
    op.drop_table("push_log")
