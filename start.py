"""Единая точка запуска для хостингов: бэкап SQLite → миграции → бот.

Запуск: python start.py (то же, что делает Dockerfile).
"""
import subprocess
import sys


def step(*cmd: str) -> None:
    code = subprocess.call(cmd)
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    step(sys.executable, "scripts/premigrate_backup.py")
    step(sys.executable, "-m", "alembic", "upgrade", "head")
    step(sys.executable, "-m", "bot.main")
