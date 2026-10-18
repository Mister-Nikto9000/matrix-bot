"""privacy: стереть точную дату рождения из payload событий birth_date_set

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-18
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Работает и в SQLite (JSON), и в PostgreSQL (jsonb): payload целиком заменяется на NULL
    op.execute("UPDATE events SET payload = NULL WHERE name = 'birth_date_set'")


def downgrade() -> None:
    # Стёртые даты вернуть нельзя — это и есть смысл миграции
    pass
