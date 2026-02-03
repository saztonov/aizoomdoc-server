# Архитектура развёрнутого решения AIZoomDoc

## Общая схема

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              WINDOWS ПК                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  AIZoomDoc.exe (PyQt6)                                              │    │
│  │  ├── Встроенный URL: https://osa.fvds.ru                            │    │
│  │  ├── Встроенный токен: dev-static-token-default-user                │    │
│  │  └── Локальный кеш: ~/.aizoomdoc/                                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTPS (443)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           VPS (osa.fvds.ru)                                  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  Nginx (ISPmanager)                                                 │     │
│  │  ├── SSL: /var/www/httpd-cert/osa/osa.fvds.ru_le1.*                │     │
│  │  ├── Конфиг: /etc/nginx/vhosts/osa/osa.fvds.ru.conf                │     │
│  │  ├── SSE: /etc/nginx/vhosts-resources/osa.fvds.ru/proxy.conf       │     │
│  │  └── proxy_pass → 127.0.0.1:8000                                   │     │
│  └────────────────────────────────┬───────────────────────────────────┘     │
│                                   │ HTTP (8000)                              │
│                                   ▼                                          │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  Docker: aizoomdoc-server                                           │     │
│  │  ├── Image: aizoomdoc-aizoomdoc:latest                             │     │
│  │  ├── Port: 127.0.0.1:8000 → 8000                                   │     │
│  │  ├── Volumes:                                                       │     │
│  │  │   ├── ./logs:/app/logs                                          │     │
│  │  │   └── ./cache:/app/cache                                        │     │
│  │  └── Healthcheck: curl http://localhost:8000/health                │     │
│  │                                                                     │     │
│  │  ┌──────────────────────────────────────────────────────────────┐  │     │
│  │  │  FastAPI Application                                          │  │     │
│  │  │  ├── /health          - проверка состояния                   │  │     │
│  │  │  ├── /auth/*          - аутентификация (JWT)                 │  │     │
│  │  │  ├── /chats/*         - управление чатами                    │  │     │
│  │  │  ├── /chats/{id}/stream - SSE стриминг ответов               │  │     │
│  │  │  ├── /files/*         - загрузка файлов                      │  │     │
│  │  │  └── /projects/*      - дерево проектов                      │  │     │
│  │  └──────────────────────────────────────────────────────────────┘  │     │
│  └────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐     │
│  │  Docker: Portainer (опционально)                                    │     │
│  │  ├── Port: 127.0.0.1:9000 (HTTP)                                   │     │
│  │  ├── Port: 127.0.0.1:9443 (HTTPS)                                  │     │
│  │  └── Доступ: SSH туннель → http://localhost:9000                   │     │
│  └────────────────────────────────────────────────────────────────────┘     │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│  Supabase (Main)  │   │ Supabase (Projects)│   │  Cloudflare R2    │
│  ├── users        │   │  ├── projects     │   │  ├── uploads/     │
│  ├── chats        │   │  ├── documents    │   │  ├── evidence/    │
│  ├── messages     │   │  └── search_blocks│   │  └── rendered/    │
│  ├── prompts_system│   │  (read-only)      │   │                   │
│  └── user_settings│   │                   │   │                   │
└───────────────────┘   └───────────────────┘   └───────────────────┘
        │
        ▼
┌───────────────────┐
│  Google Gemini    │
│  ├── Flash model  │
│  └── Pro model    │
└───────────────────┘
```

## Структура на VPS

```
/home/osa/aizoomdoc/
├── docker-compose.yml          # Production конфиг (копия docker-compose.production.yml)
├── Dockerfile
├── .env                        # Секреты (НЕ в git!)
├── requirements.txt
├── run.py
├── app/
│   ├── main.py
│   ├── config.py
│   ├── routers/
│   ├── services/
│   ├── db/
│   └── models/
├── data/
│   └── promts/                 # Промпты для LLM
├── logs/                       # Docker volume
│   └── app.log
└── cache/                      # Docker volume
    └── evidence/
```

## Конфигурация Nginx

**Основной конфиг:** `/etc/nginx/vhosts/osa/osa.fvds.ru.conf`
- Управляется ISPmanager
- proxy_pass на 127.0.0.1:8000

**SSE настройки:** `/etc/nginx/vhosts-resources/osa.fvds.ru/proxy.conf`
```nginx
proxy_read_timeout 300s;
proxy_connect_timeout 60s;
proxy_send_timeout 60s;
proxy_http_version 1.1;
proxy_set_header Connection '';
proxy_buffering off;
proxy_cache off;
chunked_transfer_encoding on;
```

**SSL сертификаты:** `/var/www/httpd-cert/osa/`
- `osa.fvds.ru_le1.crtca` - сертификат
- `osa.fvds.ru_le1.key` - ключ

## Переменные окружения (.env)

| Переменная | Описание |
|------------|----------|
| `HOST` | 0.0.0.0 |
| `PORT` | 8000 |
| `DEBUG` | false |
| `LOG_LEVEL` | INFO или WARNING |
| `JWT_SECRET_KEY` | Секретный ключ для JWT |
| `SUPABASE_URL` | URL основной БД |
| `SUPABASE_SERVICE_KEY` | Ключ основной БД |
| `SUPABASE_PROJECTS_URL` | URL БД проектов |
| `SUPABASE_PROJECTS_SERVICE_KEY` | Ключ БД проектов |
| `R2_ENDPOINT_URL` | Cloudflare R2 endpoint |
| `R2_ACCESS_KEY_ID` | R2 access key |
| `R2_SECRET_ACCESS_KEY` | R2 secret key |
| `R2_BUCKET_NAME` | aizoomdoc |
| `DEFAULT_GEMINI_API_KEY` | Ключ Google Gemini |
| `DEFAULT_MODEL` | gemini-2.5-flash-preview-05-20 |
| `CORS_ORIGINS` | https://osa.fvds.ru |

## Команды управления

```bash
# Под пользователем osa:
cd /home/osa/aizoomdoc

# Статус
docker-compose ps

# Логи
docker-compose logs -f --tail=100

# Перезапуск
docker-compose restart

# Обновление из git
git pull
docker-compose up -d --build

# Остановка
docker-compose down
```

## Доступ к Portainer

```bash
# SSH туннель с локального ПК:
ssh -L 9000:127.0.0.1:9000 root@osa.fvds.ru

# Затем в браузере:
http://localhost:9000
```

## Клиентское приложение (AIZoomDoc.exe)

**Встроенные настройки:**
- URL сервера: `https://osa.fvds.ru`
- Статичный токен: `dev-static-token-default-user`

**Локальные данные:** `%USERPROFILE%\.aizoomdoc\`
- `config.json` - конфигурация
- `credentials.json` - сохранённый токен
- `data/` - кеш изображений

**Сборка exe:**
```bash
cd aizoomdoc-client-py
pip install pyinstaller
pyinstaller aizoomdoc.spec --clean
# Результат: dist/AIZoomDoc.exe
```

## Проверка работоспособности

1. **Сервер:**
   ```bash
   curl https://osa.fvds.ru/health
   # {"status":"healthy","version":"2.0.0"}
   ```

2. **Клиент:**
   - Запустить AIZoomDoc.exe
   - Автоматическое подключение к серверу
   - Создать чат и отправить сообщение
   - Проверить получение стримингового ответа

## Безопасность

- SSL/TLS через Let's Encrypt (автообновление ISPmanager)
- Docker привязан к localhost (127.0.0.1)
- Portainer только через SSH туннель
- Секреты в .env (не в git)
- Non-root пользователь внутри контейнера
