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

---

# Bothost (платформа с Docker, тариф Basic)

1. Репозиторий подключается к Bothost (GitHub) — ветка `claude/happy-ramanujan-8hjs3h` или `main` после слияния. Сборка идёт по `Dockerfile` в корне.
2. Переменные окружения (в панели, не в репозитории): `BOT_TOKEN`, `ADMIN_IDS`, `BOT_USERNAME` (как в вашем `.env`) и
   `DATABASE_URL=sqlite+aiosqlite:////data/bot.db` — **только если в панели есть постоянный том, смонтированный в `/data`**.
   Без постоянного тома база пропадёт при пересборке: тогда нужна внешняя PostgreSQL (`postgresql+asyncpg://…`).
3. Контейнер сам делает бэкап → миграции → запуск. Логи — в панели.
4. Перед стартом остановите бота на ПК.

Если Bothost собирает образ сам (без нашего Dockerfile) и просит команду запуска — укажите `python start.py`:
он делает бэкап, миграции и запускает бота. Если команды запуска в панели нет, он ищет `main.py`/`bot.py` — тогда напишите, добавлю файл-обёртку под это имя.

**Bothost: постоянная папка `data/`.** В файловом менеджере панели рядом с кодом есть папка `data` (её создаёт платформа, не наш репозиторий) — туда складываем базу и бэкапы:
`DATABASE_URL=sqlite+aiosqlite:///data/bot.db` и `BACKUP_DIR=data/backups`.
Проверка: после `/start` пересоберите бота — запись о вас в `/health` («Пользователей») должна сохраниться.
