FROM python:3.11-slim

# Установка системных зависимостей
# Используем libgl1 вместо устаревшего libgl1-mesa-glx
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Открываем порт
EXPOSE 8000

# Запуск через uvicorn
CMD ["python", "run.py"]
