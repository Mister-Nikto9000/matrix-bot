@echo off
rem Matrix of Destiny bot: local run on Windows without Docker (SQLite).
setlocal
cd /d "%~dp0"

if not exist ".env" (
    echo [!] .env not found. Copy .env.example to .env and fill BOT_TOKEN, ADMIN_IDS, BOT_USERNAME.
    echo     For SQLite set: DATABASE_URL=sqlite+aiosqlite:///bot.db
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    echo [1/4] .venv already exists
    goto install
)
echo [1/4] Creating virtual environment .venv ...
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
if not exist ".venv\Scripts\python.exe" (
    echo [!] Could not create .venv. Install Python 3.12+ from python.org and check "Add to PATH".
    pause
    exit /b 1
)

:install

echo [2/4] Installing requirements ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo [!] pip install failed
    pause
    exit /b 1
)

echo [3/4] Backing up bot.db before migrations ...
".venv\Scripts\python.exe" -m scripts.premigrate_backup
if errorlevel 1 (
    echo [!] backup failed - migrations not applied
    pause
    exit /b 1
)
echo Applying migrations ...
".venv\Scripts\python.exe" -m alembic upgrade head
if errorlevel 1 (
    echo [!] alembic upgrade failed
    pause
    exit /b 1
)

echo [4/4] Starting bot. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m bot.main
endlocal
