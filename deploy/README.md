# Развёртывание на VPS (Ubuntu 22.04/24.04)

Нужно: сервер с 1 ГБ ОЗУ, Python 3.11+, доступ по SSH. Подойдёт любой российский VPS.

```bash
# 1. Пользователь и код
sudo adduser --system --group --home /opt/matrix-bot matrixbot
sudo apt update && sudo apt install -y python3-venv git
sudo git clone -b claude/happy-ramanujan-8hjs3h https://github.com/Mister-Nikto9000/matrix-bot /opt/matrix-bot
cd /opt/matrix-bot
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt

# 2. Настройки: скопируйте с ПК готовые .env и (если нужна текущая база) bot.db
sudo cp /путь/.env /opt/matrix-bot/.env && sudo chmod 600 .env
# bot.db: scp bot.db root@сервер:/opt/matrix-bot/bot.db
sudo chown -R matrixbot:matrixbot /opt/matrix-bot

# 3. Автозапуск
sudo cp deploy/matrix-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now matrix-bot
journalctl -u matrix-bot -f      # лог; также logs/bot.log
```

Остановите бота на ПК до запуска на сервере: два экземпляра с одним токеном конфликтуют (getUpdates).

Обновление: `./deploy/update.sh`. Бэкапы: `backups/` (7 дней) — иногда копируйте на ПК.
Проверка: `/health` у бота. Если Telegram недоступен с сервера, `journalctl` покажет ошибки сети.
