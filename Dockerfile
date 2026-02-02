FROM python:3.11-slim

# Установка системных зависимостей
# curl нужен для healthcheck
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создание non-root пользователя для безопасности
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# Копируем зависимости и устанавливаем от root
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Создаём директории для логов и кеша
RUN mkdir -p /app/logs /app/cache && \
    chown -R appuser:appgroup /app

# Переключаемся на non-root пользователя
USER appuser

# Открываем порт
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Запуск через uvicorn
CMD ["python", "run.py"]
