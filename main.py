"""Точка входа для хостингов, которые запускают main.py: делегирует в start.py (бэкап → миграции → бот)."""
import runpy

if __name__ == "__main__":
    runpy.run_path("start.py", run_name="__main__")
