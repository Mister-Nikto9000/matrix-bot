FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Бэкап перед миграциями (только для SQLite), миграции, запуск бота
CMD ["sh", "-c", "python scripts/premigrate_backup.py && alembic upgrade head && python -m bot.main"]
