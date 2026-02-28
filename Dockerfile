FROM python:3.11-slim

WORKDIR /app

# Убираем предупреждение pip при установке от root (норма для Docker)
ENV PIP_ROOT_USER_ACTION=ignore
ENV PIP_NO_CACHE_DIR=1

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы приложения
COPY . .

# Открываем порт для HTTP health check
EXPOSE 9999

# Health check для проверки работоспособности
# Используем встроенный urllib для надежности
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9999/health', timeout=5)" || exit 1

# Запускаем приложение
# Flask слушает на 0.0.0.0:PORT (см. telegram_bot.py) — доступен снаружи контейнера
# Health check: GET http://localhost:9999/health (или порт из переменной PORT)
CMD ["python", "telegram_bot.py"]
