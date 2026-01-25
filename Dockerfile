FROM python:3.11-slim

WORKDIR /app

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
# Flask будет основным процессом для health check, бот запустится в отдельном потоке
CMD ["python", "telegram_bot.py"]
