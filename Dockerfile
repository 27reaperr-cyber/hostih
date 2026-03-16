FROM python:3.11-slim

# Системные зависимости (необходимы для asyncpg и aiohttp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Переменные окружения задаются через docker-compose / .env
ENV PYTHONUNBUFFERED=1

# Команда переопределяется в docker-compose для каждого сервиса
CMD ["python", "bot.py"]
