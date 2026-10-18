#!/usr/bin/env bash
# Обновление бота на сервере: ./deploy/update.sh
set -euo pipefail
cd /opt/matrix-bot
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart matrix-bot
sleep 3
sudo systemctl --no-pager status matrix-bot | head -5
