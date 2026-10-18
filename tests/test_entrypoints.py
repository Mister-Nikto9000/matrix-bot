"""Точки входа хостингов (start.py, main.py) запускаются из корня проекта и находят пакет bot."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_entrypoints_import_bot_package(tmp_path):
    for entry in ("start.py", "main.py"):
        proc = subprocess.run(
            [sys.executable, str(ROOT / entry)], cwd=ROOT, capture_output=True, text=True, timeout=60,
            env={"PATH": "", "SYSTEMROOT": "", "BOT_TOKEN": ""},
        )
        # без настроек бот остановится на валидации токена — но не на ModuleNotFoundError
        assert "ModuleNotFoundError" not in proc.stderr, (entry, proc.stderr)
