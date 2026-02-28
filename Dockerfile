# Сборка: запускать из корня репозитория (там же Dockerfile, requirements.txt, telegram_bot.py)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_ROOT_USER_ACTION=ignore
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала только зависимости — слой кэшируется при изменении только кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения (.dockerignore исключает .git и лишнее — меньше ошибок при сборке)
COPY . .

EXPOSE 9999

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9999/health', timeout=5)" || exit 1

CMD ["python", "telegram_bot.py"]
