@echo off
rem Matrix of Destiny bot: run 24/7 on Windows (SQLite). Restarts the bot 5 seconds after any crash.
rem Log: logs\bot.log (rotation 5 MB x 5 files). Stop: close the window or press Ctrl+C twice.
rem TIP: disable sleep mode for the PC (Settings - System - Power - Screen and sleep - Never),
rem      otherwise the bot stops answering while the computer sleeps.
setlocal
cd /d "%~dp0"

if not exist ".env" (
    echo [!] .env not found. Copy .env.example to .env and fill BOT_TOKEN, ADMIN_IDS, BOT_USERNAME.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment .venv ...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
)
if not exist ".venv\Scripts\python.exe" (
    echo [!] Could not create .venv. Install Python 3.12+ from python.org and check "Add to PATH".
    pause
    exit /b 1
)

echo [2/3] Installing requirements ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo [!] pip install failed
    pause
    exit /b 1
)

echo [3/3] Backup and migrations ...
".venv\Scripts\python.exe" -m scripts.premigrate_backup
if errorlevel 1 (
    echo [!] backup failed - migrations not applied
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m alembic upgrade head
if errorlevel 1 (
    echo [!] alembic upgrade failed
    pause
    exit /b 1
)

:loop
echo %date% %time% starting bot ...
".venv\Scripts\python.exe" -m bot.main
echo %date% %time% bot stopped with code %errorlevel%. Restarting in 5 seconds (Ctrl+C to quit) ...
timeout /t 5 /nobreak >nul
goto loop
