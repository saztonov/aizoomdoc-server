# Развертывание AIZoomDoc Server на Ubuntu VPS

Полное руководство по развертыванию сервера на VPS с Ubuntu 22.04/24.04 LTS.

---

## Архитектура системы

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              КЛИЕНТЫ                                        │
│                    (Web App / Mobile App / API)                             │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ HTTPS (443)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         UBUNTU VPS                                          │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                        NGINX                                          │  │
│  │              (Reverse Proxy + SSL/TLS + Load Balancer)               │  │
│  └─────────────────────────────┬─────────────────────────────────────────┘  │
│                                │ HTTP (8000)                                │
│                                ▼                                            │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    DOCKER CONTAINER                                   │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │              AIZoomDoc Server (FastAPI + Uvicorn)               │  │  │
│  │  │                                                                 │  │  │
│  │  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐   │  │  │
│  │  │  │   Routers   │ │  Services   │ │    LRU Render Cache     │   │  │  │
│  │  │  │  (API/Auth) │ │ (Agent/LLM) │ │  (SQLite + PNG files)   │   │  │  │
│  │  │  └─────────────┘ └─────────────┘ └─────────────────────────┘   │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                       VOLUMES                                         │  │
│  │  /var/aizoomdoc/cache    - LRU кеш рендеров (PDF→PNG)                │  │
│  │  /var/aizoomdoc/logs     - Логи LLM диалогов                         │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   SUPABASE      │    │   SUPABASE      │    │  CLOUDFLARE R2  │
│   Chat DB       │    │   Projects DB   │    │  (S3 Storage)   │
│                 │    │   (read-only)   │    │                 │
│  - users        │    │  - tree_nodes   │    │  - PDF crops    │
│  - chats        │    │  - node_files   │    │  - uploads      │
│  - settings     │    │  - annotations  │    │                 │
│  - prompts      │    │                 │    │                 │
│  - block_index  │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
          │                                           │
          └─────────────────────┬─────────────────────┘
                                │
                                ▼
                    ┌─────────────────────┐
                    │    GOOGLE GEMINI    │
                    │     (LLM API)       │
                    │                     │
                    │  - Flash (анализ)   │
                    │  - Pro (ответы)     │
                    │  - File API         │
                    └─────────────────────┘
```

---

## Требования к VPS

### Минимальные требования

| Ресурс | Минимум | Рекомендуется |
|--------|---------|---------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 40 GB SSD | 100 GB NVMe SSD |
| Network | 100 Mbps | 1 Gbps |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

### Порты

| Порт | Протокол | Назначение |
|------|----------|------------|
| 22 | TCP | SSH |
| 80 | TCP | HTTP → HTTPS redirect |
| 443 | TCP | HTTPS (API) |
| 8000 | TCP | Internal (Docker → Nginx) |

---

## Необходимые внешние сервисы

### 1. Supabase (PostgreSQL + Auth)

**Chat DB** — основная БД для пользователей и чатов:
- URL: `https://your-chat-project.supabase.co`
- Service Key: для серверного доступа

**Projects DB** — read-only БД с деревом проектов:
- URL: `https://your-projects.supabase.co`
- Service Key: для чтения данных

### 2. Cloudflare R2 (S3-совместимое хранилище)

- Endpoint: `https://account-id.r2.cloudflarestorage.com`
- Bucket: `aizoomdoc`
- Access Key / Secret Key

### 3. Google Gemini API

- API Key для моделей Flash и Pro
- Квоты: ~1M tokens/day для Flash, 100K для Pro

### 4. Домен и SSL

- Доменное имя (например, `api.aizoomdoc.com`)
- SSL сертификат (Let's Encrypt — бесплатно)

---

## Пошаговая установка

### 1. Подготовка сервера

```bash
# Подключаемся к VPS
ssh root@your-server-ip

# Обновляем систему
apt update && apt upgrade -y

# Устанавливаем необходимые пакеты
apt install -y \
    curl \
    git \
    ufw \
    fail2ban \
    htop \
    ncdu

# Создаем пользователя для приложения
adduser aizoomdoc
usermod -aG sudo aizoomdoc

# Переключаемся на нового пользователя
su - aizoomdoc
```

### 2. Настройка Firewall (UFW)

```bash
# Настраиваем firewall
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw enable

# Проверяем статус
sudo ufw status
```

### 3. Установка Docker

```bash
# Устанавливаем Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Добавляем пользователя в группу docker
sudo usermod -aG docker aizoomdoc

# Перелогиниваемся для применения изменений
exit
su - aizoomdoc

# Проверяем Docker
docker --version
docker compose version
```

### 4. Установка Nginx

```bash
sudo apt install -y nginx

# Проверяем статус
sudo systemctl status nginx
```

### 5. Клонирование проекта

```bash
# Создаем директорию для приложений
mkdir -p ~/apps
cd ~/apps

# Клонируем репозиторий (или копируем файлы)
git clone https://github.com/your-org/aizoomdoc-server.git
cd aizoomdoc-server

# Или копируем через scp с локальной машины:
# scp -r ./aizoomdoc-server aizoomdoc@your-server-ip:~/apps/
```

### 6. Настройка переменных окружения

```bash
# Копируем пример конфигурации
cp env.example .env

# Редактируем конфигурацию
nano .env
```

**Обязательные переменные для продакшена:**

```env
# Server
HOST=0.0.0.0
PORT=8000
DEBUG=false
LOG_LEVEL=INFO

# JWT (сгенерируйте: python -c "import secrets; print(secrets.token_urlsafe(64))")
JWT_SECRET_KEY=YOUR_VERY_SECURE_RANDOM_KEY_64_CHARS

# Supabase Chat DB
SUPABASE_URL=https://your-chat-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-key

# Supabase Projects DB
SUPABASE_PROJECTS_URL=https://your-projects.supabase.co
SUPABASE_PROJECTS_ANON_KEY=your-projects-anon-key
SUPABASE_PROJECTS_SERVICE_KEY=your-projects-service-key

# Cloudflare R2
R2_ENDPOINT_URL=https://account-id.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=your-r2-access-key
R2_SECRET_ACCESS_KEY=your-r2-secret-key
R2_BUCKET_NAME=aizoomdoc
R2_PUBLIC_DOMAIN=https://cdn.aizoomdoc.com

# LLM
DEFAULT_GEMINI_API_KEY=your-gemini-api-key
DEFAULT_MODEL=gemini-2.5-flash-preview-05-20

# Evidence Cache (LRU)
EVIDENCE_CACHE_ENABLED=true
EVIDENCE_CACHE_DIR=/app/cache
EVIDENCE_CACHE_MAX_MB=2000
EVIDENCE_CACHE_TTL_DAYS=14

# CORS (укажите домен фронтенда)
CORS_ORIGINS=https://app.aizoomdoc.com,https://aizoomdoc.com
```

### 7. Создание директорий для данных

```bash
# Создаем директории для persistent данных
sudo mkdir -p /var/aizoomdoc/cache
sudo mkdir -p /var/aizoomdoc/logs
sudo chown -R aizoomdoc:aizoomdoc /var/aizoomdoc
```

### 8. Настройка Docker Compose для продакшена

Создайте `docker-compose.prod.yml`:

```bash
nano docker-compose.prod.yml
```

```yaml
services:
  aizoomdoc-server:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: aizoomdoc-server
    restart: always
    ports:
      - "127.0.0.1:8000:8000"
    env_file:
      - .env
    volumes:
      - /var/aizoomdoc/cache:/app/cache
      - /var/aizoomdoc/logs:/app/logs
      - ./data/promts:/app/data/promts:ro
    environment:
      - PYTHONUNBUFFERED=1
      - TZ=Europe/Moscow
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    deploy:
      resources:
        limits:
          memory: 4G
        reservations:
          memory: 1G
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "5"
```

### 9. Сборка и запуск Docker

```bash
# Собираем образ
docker compose -f docker-compose.prod.yml build

# Запускаем в фоне
docker compose -f docker-compose.prod.yml up -d

# Проверяем статус
docker compose -f docker-compose.prod.yml ps

# Смотрим логи
docker compose -f docker-compose.prod.yml logs -f
```

### 10. Настройка Nginx + SSL

#### Установка Certbot для Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
```

#### Создание конфигурации Nginx

```bash
sudo nano /etc/nginx/sites-available/aizoomdoc
```

```nginx
# Upstream для API сервера
upstream aizoomdoc_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

# HTTP → HTTPS редирект
server {
    listen 80;
    listen [::]:80;
    server_name api.aizoomdoc.com;
    
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS сервер
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name api.aizoomdoc.com;

    # SSL сертификаты (будут созданы certbot)
    ssl_certificate /etc/letsencrypt/live/api.aizoomdoc.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.aizoomdoc.com/privkey.pem;
    
    # SSL настройки
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    # HSTS
    add_header Strict-Transport-Security "max-age=63072000" always;
    
    # Gzip
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript application/xml;

    # Лимиты
    client_max_body_size 100M;
    
    # Логи
    access_log /var/log/nginx/aizoomdoc.access.log;
    error_log /var/log/nginx/aizoomdoc.error.log;

    # API endpoints
    location / {
        proxy_pass http://aizoomdoc_backend;
        proxy_http_version 1.1;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (для SSE streaming)
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        
        # Таймауты для долгих запросов (LLM)
        proxy_connect_timeout 60s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
    
    # Health check endpoint (без логирования)
    location /health {
        proxy_pass http://aizoomdoc_backend/health;
        access_log off;
    }
}
```

#### Активация конфигурации

```bash
# Создаем симлинк
sudo ln -s /etc/nginx/sites-available/aizoomdoc /etc/nginx/sites-enabled/

# Удаляем дефолтный сайт
sudo rm -f /etc/nginx/sites-enabled/default

# Проверяем конфигурацию
sudo nginx -t

# Перезапускаем nginx
sudo systemctl reload nginx
```

#### Получение SSL сертификата

```bash
# Создаем директорию для certbot
sudo mkdir -p /var/www/certbot

# Получаем сертификат
sudo certbot --nginx -d api.aizoomdoc.com

# Автообновление (добавляется автоматически, проверяем)
sudo systemctl status certbot.timer
```

### 11. Настройка автозапуска

```bash
# Создаем systemd сервис для docker compose
sudo nano /etc/systemd/system/aizoomdoc.service
```

```ini
[Unit]
Description=AIZoomDoc Server
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=aizoomdoc
Group=aizoomdoc
WorkingDirectory=/home/aizoomdoc/apps/aizoomdoc-server
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

```bash
# Активируем сервис
sudo systemctl daemon-reload
sudo systemctl enable aizoomdoc
sudo systemctl start aizoomdoc
```

---

## Проверка развертывания

### Health Check

```bash
# Локально
curl http://localhost:8000/health

# Через Nginx
curl https://api.aizoomdoc.com/health
```

Ожидаемый ответ:
```json
{
  "status": "healthy",
  "version": "2.0.0",
  "service": "aizoomdoc-server"
}
```

### API Documentation

Откройте в браузере:
- Swagger UI: `https://api.aizoomdoc.com/docs`
- ReDoc: `https://api.aizoomdoc.com/redoc`

### Тест аутентификации

```bash
curl -X POST https://api.aizoomdoc.com/auth/exchange \
  -H "Content-Type: application/json" \
  -d '{"static_token": "your-secure-token"}'
```

---

## Мониторинг и обслуживание

### Просмотр логов

```bash
# Логи Docker контейнера
docker compose -f docker-compose.prod.yml logs -f --tail=100

# Логи Nginx
sudo tail -f /var/log/nginx/aizoomdoc.access.log
sudo tail -f /var/log/nginx/aizoomdoc.error.log

# Логи LLM диалогов
ls -la /var/aizoomdoc/logs/
```

### Мониторинг ресурсов

```bash
# Общее состояние системы
htop

# Использование диска
df -h
ncdu /var/aizoomdoc

# Docker статистика
docker stats

# Состояние кеша рендеров
ls -la /var/aizoomdoc/cache/
du -sh /var/aizoomdoc/cache/
```

### Обновление приложения

```bash
cd ~/apps/aizoomdoc-server

# Получаем обновления
git pull origin main

# Пересобираем и перезапускаем
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d

# Проверяем статус
docker compose -f docker-compose.prod.yml ps
```

### Очистка кеша рендеров

```bash
# Полная очистка (при необходимости)
docker compose -f docker-compose.prod.yml exec aizoomdoc-server \
  python -c "from app.services.render_cache import get_render_cache; print(get_render_cache().clear())"

# Или вручную
sudo rm -rf /var/aizoomdoc/cache/renders/*
sudo rm -f /var/aizoomdoc/cache/cache_metadata.db
```

### Backup

```bash
# Backup конфигурации
tar -czvf aizoomdoc-config-$(date +%Y%m%d).tar.gz .env docker-compose.prod.yml

# Backup логов LLM
tar -czvf aizoomdoc-logs-$(date +%Y%m%d).tar.gz /var/aizoomdoc/logs/
```

---

## Troubleshooting

### Контейнер не запускается

```bash
# Проверяем логи
docker compose -f docker-compose.prod.yml logs

# Проверяем .env файл
cat .env | grep -v "^#" | grep -v "^$"

# Проверяем права на директории
ls -la /var/aizoomdoc/
```

### 502 Bad Gateway

```bash
# Проверяем, что контейнер запущен
docker compose -f docker-compose.prod.yml ps

# Проверяем, что порт слушается
sudo netstat -tlpn | grep 8000

# Проверяем логи nginx
sudo tail -20 /var/log/nginx/aizoomdoc.error.log
```

### Ошибки подключения к Supabase

```bash
# Проверяем DNS
nslookup your-project.supabase.co

# Проверяем подключение
curl -I https://your-project.supabase.co

# Проверяем ключи
docker compose -f docker-compose.prod.yml exec aizoomdoc-server \
  python -c "from app.config import settings; print(settings.supabase_url)"
```

### Высокое использование памяти

```bash
# Проверяем использование
docker stats --no-stream

# Перезапуск контейнера
docker compose -f docker-compose.prod.yml restart

# Очистка неиспользуемых образов
docker system prune -a
```

---

## Безопасность

### Checklist для продакшена

- [ ] Все секреты сгенерированы случайно (JWT_SECRET_KEY, static_tokens)
- [ ] DEBUG=false
- [ ] Firewall настроен (UFW)
- [ ] Fail2ban установлен и настроен
- [ ] SSL/TLS включен (Let's Encrypt)
- [ ] CORS настроен только для нужных доменов
- [ ] Регулярные обновления системы (`unattended-upgrades`)
- [ ] Логирование включено
- [ ] Мониторинг настроен
- [ ] Backup настроен

### Настройка Fail2ban

```bash
sudo nano /etc/fail2ban/jail.local
```

```ini
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true

[nginx-http-auth]
enabled = true
```

```bash
sudo systemctl restart fail2ban
```

### Автообновления безопасности

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## Масштабирование

### Вертикальное (увеличение ресурсов VPS)

Для увеличения производительности:
- Добавьте CPU/RAM через панель провайдера
- Увеличьте `EVIDENCE_CACHE_MAX_MB` пропорционально

### Горизонтальное (несколько серверов)

Для высокой нагрузки рассмотрите:
1. **Load Balancer** (nginx upstream с несколькими backend)
2. **Общий кеш** (Redis вместо локального SQLite)
3. **CDN** для статики (Cloudflare)

---

## Полезные команды

```bash
# Статус всех сервисов
sudo systemctl status nginx docker aizoomdoc

# Быстрый рестарт всего
sudo systemctl restart aizoomdoc && sudo systemctl reload nginx

# Проверка использования диска кешем
du -sh /var/aizoomdoc/cache/

# Количество файлов в кеше
find /var/aizoomdoc/cache/renders -type f | wc -l

# Мониторинг в реальном времени
watch -n 5 'docker stats --no-stream && echo "---" && df -h /var/aizoomdoc'
```

