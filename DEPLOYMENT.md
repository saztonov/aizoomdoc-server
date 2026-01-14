# Развертывание AIZoomDoc Server

## Подготовка окружения

### 1. Установка Python зависимостей

```bash
# Создать виртуальное окружение
python -m venv venv

# Активировать
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt
```

### 2. Настройка переменных окружения

Создайте файл `.env` на основе `env.example`:

```bash
cp env.example .env
```

Заполните обязательные параметры:

```env
# Генерация секретного ключа для JWT
# Python: import secrets; print(secrets.token_urlsafe(32))
JWT_SECRET_KEY=ваш-сгенерированный-ключ

# Supabase основная БД (для чатов и пользователей)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=ваш-service-key

# Supabase Projects DB (для дерева проектов)
SUPABASE_PROJECTS_URL=https://your-projects.supabase.co
SUPABASE_PROJECTS_SERVICE_KEY=ваш-projects-service-key

# Cloudflare R2 или S3
R2_ENDPOINT_URL=https://account-id.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=ваш-access-key
R2_SECRET_ACCESS_KEY=ваш-secret-key
R2_BUCKET_NAME=aizoomdoc

# Gemini API (опционально, пользователи могут иметь свои)
DEFAULT_GEMINI_API_KEY=ваш-gemini-api-key
```

## Развертывание базы данных

### 1. Создание таблиц

Подключитесь к вашей Supabase БД и выполните миграцию:

```bash
# Через psql
psql <connection-string> < migrations/001_create_users_prompts_settings.sql

# Или через Supabase Dashboard
# SQL Editor → вставить содержимое файла → Run
```

### 2. Загрузка промптов

После создания таблиц, обновите содержимое системных промптов.

Скопируйте содержимое файлов из исходного проекта:
- `C:\Users\postoev.e.v\CursorProjects\aizoomdoc\aizoomdoc\data\llm_system_prompt.txt`
- `C:\Users\postoev.e.v\CursorProjects\aizoomdoc\aizoomdoc\data\json_annotation_prompt.txt`
- `C:\Users\postoev.e.v\CursorProjects\aizoomdoc\aizoomdoc\data\html_ocr_prompt.txt`
- `C:\Users\postoev.e.v\CursorProjects\aizoomdoc\aizoomdoc\data\flash_extractor_prompt.txt`

Выполните SQL запросы для обновления:

```sql
-- llm_system
UPDATE prompts_system 
SET content = '<содержимое llm_system_prompt.txt>' 
WHERE name = 'llm_system';

-- json_annotation
UPDATE prompts_system 
SET content = '<содержимое json_annotation_prompt.txt>' 
WHERE name = 'json_annotation';

-- html_ocr
UPDATE prompts_system 
SET content = '<содержимое html_ocr_prompt.txt>' 
WHERE name = 'html_ocr';

-- flash_extractor
UPDATE prompts_system 
SET content = '<содержимое flash_extractor_prompt.txt>' 
WHERE name = 'flash_extractor';
```

### 3. Создание тестового пользователя

Миграция автоматически создает двух тестовых пользователей:

```
username: admin
static_token: dev-static-token-admin-12345

username: test_user
static_token: dev-static-token-test-67890
```

**ВАЖНО:** В продакшене смените эти токены на безопасные!

## Запуск сервера

### Режим разработки

```bash
# Через run.py
python run.py

# Или через uvicorn напрямую
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Продакшн

```bash
# С несколькими воркерами
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Или через gunicorn
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## Проверка работы

### 1. Health Check

```bash
curl http://localhost:8000/health
```

Ожидаемый ответ:
```json
{
  "status": "healthy",
  "version": "2.0.0",
  "service": "aizoomdoc-server"
}
```

### 2. Аутентификация

```bash
curl -X POST http://localhost:8000/auth/exchange \
  -H "Content-Type: application/json" \
  -d '{"static_token": "dev-static-token-admin-12345"}'
```

Ожидаемый ответ:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "uuid",
    "username": "admin",
    "status": "active",
    "created_at": "2026-01-13T..."
  }
}
```

### 3. API Documentation

Откройте в браузере:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Мониторинг и логи

### Логирование

Сервер использует стандартное логирование Python. Уровень логов настраивается через `LOG_LEVEL` в `.env`:

```env
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

### Структура логов

```
2026-01-13 12:00:00 - app.main - INFO - Starting AIZoomDoc Server...
2026-01-13 12:00:01 - app.routers.auth - INFO - User admin logged in
2026-01-13 12:00:05 - app.services.agent_service - INFO - Processing message in chat uuid...
```

## Развертывание в облаке

### Docker (опционально)

Создайте `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Сборка и запуск:

```bash
docker build -t aizoomdoc-server .
docker run -p 8000:8000 --env-file .env aizoomdoc-server
```

### Переменные окружения в продакшене

В продакшене используйте безопасные методы хранения секретов:

- **AWS**: AWS Secrets Manager
- **Google Cloud**: Secret Manager
- **Azure**: Key Vault
- **Kubernetes**: Secrets
- **Heroku**: Config Vars

## Troubleshooting

### Ошибка подключения к Supabase

```
Error: Could not connect to Supabase
```

Проверьте:
1. Правильность `SUPABASE_URL` и `SUPABASE_SERVICE_KEY`
2. Сетевой доступ к Supabase (firewall, VPN)
3. Квоты Supabase проекта

### Ошибка JWT

```
Error: Invalid token
```

Проверьте:
1. `JWT_SECRET_KEY` установлен и не изменился
2. Токен не истек (TTL = 60 минут по умолчанию)
3. Формат токена: `Bearer <token>`

### Ошибка Gemini API

```
Error: Gemini API key not configured
```

Проверьте:
1. У пользователя есть настроенный `gemini_api_key` ИЛИ
2. Установлен `DEFAULT_GEMINI_API_KEY` в `.env`

## Безопасность

### Продакшн checklist

- [ ] Сменить все тестовые static_token на случайные безопасные
- [ ] Сгенерировать новый `JWT_SECRET_KEY` (минимум 32 символа)
- [ ] Включить HTTPS для API
- [ ] Настроить CORS только для доверенных доменов
- [ ] Регулярно ротировать секреты
- [ ] Настроить rate limiting
- [ ] Включить мониторинг и алерты
- [ ] Настроить backup БД

### Рекомендации

1. **Никогда не коммитьте `.env`** в git
2. **Используйте разные ключи** для dev/staging/prod
3. **Ограничьте доступ** к service keys Supabase
4. **Мониторьте** использование API ключей Gemini

## Поддержка

При возникновении проблем:

1. Проверьте логи сервера
2. Проверьте документацию API: `/docs`
3. Проверьте статус сервисов (Supabase, R2)
4. Создайте issue в репозитории

