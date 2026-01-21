# AIZoomDoc v2 — Архитектура клиент-сервер

**Дата**: 2026-01-19  
**Версия**: 2.0.0  
**Статус**: Production Ready

---

## 📋 Оглавление

1. [Обзор системы](#обзор-системы)
2. [Компоненты архитектуры](#компоненты-архитектуры)
3. [Сервер (Backend)](#сервер-backend)
4. [Клиент (Frontend)](#клиент-frontend)
5. [Взаимодействие компонентов](#взаимодействие-компонентов)
6. [Потоки данных](#потоки-данных)
7. [Безопасность](#безопасность)
8. [Масштабирование](#масштабирование)

---

## 🏗 Обзор системы

**AIZoomDoc v2** — это двухуровневая система анализа технической документации с использованием LLM:

- **Клиент**: Python-приложение (CLI/GUI) для локального взаимодействия с пользователем
- **Сервер**: FastAPI backend, предоставляющий REST API и WebSocket стриминг
- **Хранилище**: Supabase (основная БД + БД проектов) + S3/R2 (файлы)
- **LLM**: Google Gemini API (per-user ключи)

```
┌─────────────────────────────────────────────────────────────────┐
│                        AIZoomDoc v2                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────────┐              ┌──────────────────────────┐  │
│  │  Клиент (Python) │              │   Сервер (FastAPI)      │  │
│  ├──────────────────┤              ├──────────────────────────┤  │
│  │ • CLI            │              │ • Auth (JWT)             │  │
│  │ • GUI (Qt/Tkinter)              │ • Routers (6)            │  │
│  │ • Config Manager │── HTTP/WS───▶│ • Services (9+)          │  │
│  │ • Stream Handler │              │ • Error Handlers         │  │
│  │                  │              │ • Middleware (CORS)      │  │
│  └──────────────────┘              └──────────────────────────┘  │
│                                             │                     │
│                                             │                     │
│                    ┌────────────────────────┼────────────────┐   │
│                    │                        │                │   │
│            ┌───────▼──────┐      ┌──────────▼──────┐   ┌─────▼───┐
│            │ Supabase DB  │      │  S3/R2 Storage  │   │ Gemini   │
│            │ • Chats      │      │  • Files        │   │ API      │
│            │ • Users      │      │  • Crops        │   │          │
│            │ • Prompts    │      │                 │   │          │
│            │ • Settings   │      │                 │   │          │
│            └──────────────┘      └─────────────────┘   └──────────┘
│                                                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🧩 Компоненты архитектуры

### Уровень представления (Client)
- **CLI интерфейс** (click + rich) — текстовый интерфейс командной строки
- **GUI** — графический интерфейс (Qt или Tkinter)
- **Config Manager** — управление конфигурацией и токенами

### Уровень API (Server)
- **HTTP API** (FastAPI) — REST endpoints
- **WebSocket** — стриминг событий в реальном времени
- **Middleware** — CORS, логирование, обработка ошибок

### Уровень приложения (Services)
- **Auth Service** — JWT валидация, статичный токен → JWT
- **Chat Service** — управление чатами и сообщениями
- **LLM Service** — интеграция с Gemini API
- **Search Service** — полнотекстовый поиск
- **File Service** — загрузка и обработка файлов
- **Image Service** — OCR, zoom, crop
- **Agent Service** — оркестрация пайплайна
- **Document Extract Service** — универсальное извлечение фактов и таблиц
- **Quality Gate** — проверка достаточности доказательств и триггер followup

### Уровень данных (Database & Storage)
- **Supabase** (основная БД) — пользователи, чаты, промпты, настройки
- **Supabase Projects** (read-only) — дерево проектов, файлы
- **S3/R2** — хранилище загруженных файлов

---

## 🖥 Сервер (Backend)

### Структура проекта

```
aizoomdoc-server/
├── app/
│   ├── main.py                  # Точка входа FastAPI
│   ├── config.py                # Конфигурация (переменные окружения)
│   │
│   ├── core/                    # Ядро приложения
│   │   ├── auth.py             # JWT утилиты
│   │   └── dependencies.py      # FastAPI dependencies
│   │
│   ├── models/                  # Pydantic модели
│   │   ├── api.py              # API контракты (запросы/ответы)
│   │   ├── internal.py         # Внутренние модели БД
│   │   └── llm_schemas.py      # Схемы для LLM
│   │
│   ├── routers/                 # API маршруты
│   │   ├── auth.py             # POST /auth/exchange (статичный токен → JWT)
│   │   ├── user.py             # GET /me, POST /settings
│   │   ├── chats.py            # CRUD операции с чатами и сообщениями
│   │   ├── prompts.py          # GET /prompts (система + пользовательские)
│   │   ├── files.py            # POST /files/upload
│   │   └── projects.py         # GET /projects/tree (read-only)
│   │
│   ├── db/                      # Клиенты внешних сервисов
│   │   ├── supabase_client.py              # Основная БД клиент
│   │   ├── supabase_projects_client.py     # БД проектов клиент
│   │   └── s3_client.py                    # S3/R2 хранилище клиент
│   │
│   └── services/                # Бизнес-логика
│       ├── llm_service.py      # Google Gemini API интеграция
│       ├── search_service.py   # Поиск в документах
│       ├── image_service.py    # OCR, zoom, квадранты
│       ├── agent_service.py    # Оркестрация пайплайна
│       ├── document_extract_service.py # Извлечение фактов/таблиц
│       ├── queue_service.py    # Управление очередью запросов
│       ├── deletion_service.py # Background удаление файлов
│       ├── html_ocr_service.py # HTML парсинг для OCR
│       ├── llm_logger.py       # Логирование LLM диалогов
│       └── render_cache.py     # Кэширование рендерированных блоков
│
├── migrations/                  # SQL миграции
│   ├── 001_create_users_prompts_settings.sql
│   └── 002_add_user_roles.sql
│
├── data/                        # Данные приложения
│   └── promts/                 # Системные промпты
│       ├── llm_system_prompt.txt
│       ├── flash_answer_prompt.txt
│       ├── pro_answer_prompt.txt
│       ├── flash_extractor_prompt.txt
│       ├── html_ocr_prompt.txt
│       └── json_annotation_prompt.txt
│       ├── analysis_router_prompt.txt
│       ├── document_extract_prompt.txt
│       └── roi_request_prompt.txt
│
├── requirements.txt             # Python зависимости
├── env.example                 # Пример конфигурации
└── README.md
```

### Основные технологии

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| **Framework** | FastAPI | 0.115.6 |
| **Server** | Uvicorn | 0.34.0 |
| **ORM/DB** | Supabase SDK | 2.27.2 |
| **Storage** | Boto3 (S3/R2) | 1.35.95 |
| **LLM** | Google Genai | ≥1.0.0 |
| **Auth** | PyJWT | 2.10.1 |
| **Validation** | Pydantic | 2.11.7 |
| **Image Processing** | PyMuPDF | 1.25.2 |

### API Роутеры

#### 1. Auth (`/auth`)
```
POST /auth/exchange
  Обмен статичного токена на JWT
  Request: { "static_token": "..." }
  Response: { "access_token", "expires_in", "user" }
```

#### 2. User (`/`)
```
GET /me
  Текущий пользователь + настройки
  Response: { "user", "settings", "gemini_api_key_configured" }

POST /settings
  Обновление настроек пользователя
  Body: { "model_profile": "simple|complex", "selected_role_prompt_id": "..." }
```

#### 3. Chats (`/chats`)
```
GET /chats
  Список чатов пользователя
  
POST /chats
  Создание нового чата
  Body: { "title", "description" }

GET /chats/{chat_id}
  Информация о чате

POST /chats/{chat_id}/messages
  Отправка сообщения
  Body: { "content", "attached_file_ids" }

GET /chats/{chat_id}/messages
  История сообщений

WebSocket /chats/{chat_id}/stream
  Стриминг ответа LLM
```

#### 4. Prompts (`/prompts`)
```
GET /prompts
  Список системных и пользовательских промптов

GET /prompts/roles
  Доступные роли пользователя
```

#### 5. Files (`/files`)
```
POST /files/upload
  Загрузка файла на S3/R2
  Form-data: file (multipart)
  Response: { "id", "filename", "url", "size" }
```

#### 6. Projects (`/projects`)
```
GET /projects/tree
  Дерево проектов (read-only из БД проектов)

GET /projects/search
  Поиск документов по названию
  Query: search_query
```

### Жизненный цикл приложения

```python
# Startup
1. Инициализация логирования
2. Запуск DeletionService (фоновое удаление файлов)
3. Запуск QueueService (управление очередью запросов)
4. Инициализация CORS middleware

# Request handling
1. Проверка JWT токена (кроме /health, /docs)
2. Выполнение бизнес-логики
3. Возврат JSON ответа или WebSocket события

# Shutdown
1. Остановка QueueService
2. Остановка DeletionService
3. Закрытие соединений
```

---

## 👤 Клиент (Frontend)

### Структура проекта

```
aizoomdoc-client-py/
├── src/aizoomdoc_client/
│   ├── __init__.py          # Публичный API
│   │
│   ├── client.py            # Основной класс AIZoomDocClient
│   │                         # • authenticate()
│   │                         # • create_chat()
│   │                         # • send_message()
│   │                         # • upload_file()
│   │                         # • get_projects_tree()
│   │
│   ├── http_client.py       # HTTPClient с авто-refresh JWT
│   │                         # • Управление токенами
│   │                         # • Повторные попытки
│   │                         # • Обработка ошибок
│   │
│   ├── config.py            # ConfigManager
│   │                         # • ~/.aizoomdoc/config.json
│   │                         # • Сохранение токенов
│   │                         # • Переменные окружения
│   │
│   ├── models.py            # Pydantic модели
│   │                         # • UserInfo, ChatResponse
│   │                         # • StreamEvent, FileUploadResponse
│   │
│   ├── exceptions.py        # Иерархия исключений
│   │                         # • AuthenticationError
│   │                         # • TokenExpiredError
│   │                         # • APIError, ServerError
│   │
│   └── cli.py               # CLI интерфейс (click + rich)
│       ├── cmd_auth        # login, logout, health
│       ├── cmd_chat        # new, list, use, send, history
│       ├── cmd_settings    # set-model, set-role, list-roles
│       ├── cmd_files       # upload, list, delete
│       └── cmd_projects    # tree, search
│
├── run_gui.py               # GUI интерфейс
│
├── requirements.txt         # Зависимости
├── setup.py                 # Packaging
└── README.md
```

### Основные технологии

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| **HTTP Client** | httpx | последняя |
| **Streaming** | httpx-sse | последняя |
| **CLI Framework** | click | последняя |
| **UI Output** | rich | последняя |
| **WebSockets** | websockets | последняя |
| **Validation** | pydantic | 2.x |
| **GUI (опционально)** | PyQt6 / PySimpleGUI | последняя |

### Основные классы

#### AIZoomDocClient

```python
class AIZoomDocClient:
    def __init__(
        self,
        server_url: str = "http://localhost:8000",
        static_token: str = None,
        config_manager: ConfigManager = None
    )
    
    # Аутентификация
    async def authenticate() -> UserInfo
    
    # Управление чатами
    async def create_chat(title: str, description: str = None) -> ChatResponse
    async def list_chats() -> List[ChatResponse]
    async def get_chat(chat_id: UUID) -> ChatResponse
    async def delete_chat(chat_id: UUID)
    
    # Отправка сообщений
    async def send_message(
        chat_id: UUID,
        content: str,
        attached_file_ids: List[UUID] = None
    ) -> Iterator[StreamEvent]  # Стриминг событий
    
    async def get_chat_history(chat_id: UUID) -> List[MessageResponse]
    
    # Файлы
    async def upload_file(file_path: Path) -> FileInfo
    async def get_files() -> List[FileInfo]
    
    # Настройки пользователя
    async def get_me() -> UserMeResponse
    async def update_settings(
        model_profile: Literal["simple", "complex"] = None,
        selected_role_prompt_id: UUID = None
    )
    
    # Промпты и роли
    async def get_prompts() -> PromptResponse
    async def get_user_roles() -> List[PromptUserRole]
    
    # Проекты (read-only)
    async def get_projects_tree() -> TreeNode
    async def search_projects(query: str) -> List[TreeNode]
```

#### ConfigManager

```python
class ConfigManager:
    def __init__(self, config_dir: Path = None)
    
    def save_token_data(token_data: TokenData)
    def load_token_data() -> TokenData | None
    def clear_token_data()
    
    def get_server_url() -> str  # из .env или config.json
    def get_last_chat_id() -> UUID | None
    def set_last_chat_id(chat_id: UUID)
```

### CLI Интерфейс

Построен с помощью `click` и `rich`:

```bash
aizoomdoc auth login --token TOKEN         # Авторизация
aizoomdoc auth logout                      # Выход
aizoomdoc auth health                      # Проверка состояния

aizoomdoc user me                          # Текущий пользователь
aizoomdoc user settings                    # Показать настройки

aizoomdoc chat new "Название"              # Новый чат
aizoomdoc chat list                        # Список чатов
aizoomdoc chat use <CHAT_ID>               # Выбрать активный
aizoomdoc chat send "Сообщение"            # Отправить (со стримингом)
aizoomdoc chat history                     # История

aizoomdoc settings set-model simple|complex
aizoomdoc settings set-role "Инженер"
aizoomdoc settings set-role none           # Очистить роль
aizoomdoc settings list-roles              # Доступные роли

aizoomdoc file upload <PATH>               # Загрузить файл
aizoomdoc file list                        # Список файлов

aizoomdoc projects tree                    # Дерево проектов
aizoomdoc projects search "запрос"         # Поиск документов
```

---

## 🔄 Взаимодействие компонентов

### Сценарий 1: Авторизация пользователя

```
Клиент (CLI)                                Сервер (FastAPI)
    │                                              │
    ├─ "aizoomdoc login --token ABC123"          │
    │                                              │
    ├─ POST /auth/exchange                       │
    │    { "static_token": "ABC123" }────────────┤
    │                                              │
    │                                    1. Проверка токена
    │                                    2. Создание/обновление пользователя
    │                                    3. Генерация JWT
    │                                              │
    │◀─ 200 OK                                    │
    │    { "access_token": "eyJ...",             │
    │      "expires_in": 3600,                    │
    │      "user": {...}                          │
    │    }                                         │
    │                                              │
    ├─ Сохранение токена в ~/.aizoomdoc/config.json
    │
    └─ Готов к использованию ✓
```

### Сценарий 2: Отправка сообщения со стримингом

```
Клиент (CLI)                                Сервер (FastAPI)
    │                                              │
    ├─ "aizoomdoc chat send 'Вопрос'"            │
    │                                              │
    ├─ WebSocket /chats/{id}/stream?token=...───┤
    │                                              │
    │                                    1. Валидация JWT
    │                                    2. Сохранение сообщения в БД
    │                                    3. Запуск Agent Service:
    │                                       a. Search (поиск контекста)
    │                                       b. Flash LLM (быстрый анализ)
    │                                       c. Pro LLM (финальный ответ)
    │◀─ SSE event { "event": "phase_started", ... }
    │◀─ SSE event { "event": "llm_token", "data": {"token": "Hello"} }
    │◀─ SSE event { "event": "llm_token", "data": {"token": " world"} }
    │◀─ SSE event { "event": "phase_completed", ... }
    │◀─ SSE event { "event": "completed", ... }
    │                                              │
    └─ Вывод в консоль со streaming эффектом ✓
```

### Сценарий 3: Загрузка и анализ файла

```
Клиент                                      Сервер
    │                                              │
    ├─ POST /files/upload                        │
    │    (multipart: file)─────────────────────┤
    │                                              │
    │                                    1. Валидация файла
    │                                    2. Загрузка в S3/R2
    │                                    3. Запуск OCR:
    │                                       • PyMuPDF экстракция
    │                                       • Gemini HTML OCR
    │                                       • Сохранение результата
    │◀─ 200 OK { "id": "...", "url": "..." }
    │                                              │
    ├─ POST /chats/{id}/messages                 │
    │    { "content": "Проанализируй",            │
    │      "attached_file_ids": ["..."] }────────┤
    │                                              │
    │                                    1. Получить файл из S3
    │                                    2. Использовать в контексте
    │                                    3. Запустить Agent Pipeline
    │◀─ WebSocket stream (как сценарий 2)
    │                                              │
    └─ Результат с ссылками на компоненты файла ✓
```

---

## 📊 Потоки данных

### Основной поток обработки запроса

```
User Input (CLI/GUI)
    │
    ▼
APIZoomDocClient.send_message()
    │
    ▼
HTTPClient.post_with_streaming()
    │
    ├─ Подготовка JWT токена (авто-refresh если нужно)
    ├─ Отправка на WebSocket /chats/{id}/stream
    └─ Установка SSE соединения
    │
    ┌─────────────────────────────────────────────────────────┐
    │                   SERVER SIDE                            │
    │                                                           │
    ├─ FastAPI роутер (chats.py) получает запрос              │
    │  └─ Валидация JWT (auth.py)                              │
    │     └─ Сохранение сообщения в Supabase                  │
    │        └─ Вызов AgentService.process_message()           │
    │           │                                               │
    │           ├─1─ SearchService.find_context()              │
    │           │    └─ Полнотекстовый поиск в Supabase        │
    │           │                                               │
    │           ├─2─ LLMService.flash_mode() (simple)           │
    │           │    ├─ Формирование контекста                 │
    │           │    ├─ Вызов Gemini Flash API                 │
    │           │    └─ Стриминг токенов                       │
    │           │                                               │
    │           ├─3─ LLMService.pro_mode() (complex)            │
    │           │    ├─ Анализ ответа Flash                    │
    │           │    ├─ Вызов Gemini Pro API                   │
    │           │    └─ Финальный ответ                        │
    │           │                                               │
    │           └─ Логирование (llm_logger.py)                 │
    │                                                           │
    │   Отправка событий SSE обратно клиенту:                 │
    │   ├─ phase_started                                       │
    │   ├─ llm_token (повторяется для каждого токена)         │
    │   ├─ phase_completed                                     │
    │   └─ completed                                            │
    └─────────────────────────────────────────────────────────┘
    │
    ▼
HTTPClient.stream_response()
    │
    ├─ Парсинг SSE событий
    ├─ Вызов обработчиков событий
    └─ Возврат итератора StreamEvent
    │
    ▼
CLI (rich) вывод результата
    │
    ├─ [Поиск документов] ...
    ├─ [Анализ Flash] Это быстрый анализ
    ├─ [Формирование ответа Pro] Более подробный ответ...
    └─ ✓ Завершено
```

### Управление токенами

```
Клиент                          HTTPClient                 Сервер

Static Token
    │
    ▼
Запрос авторизации
    ├─ POST /auth/exchange
    │  { "static_token": "..." }
    │
    ▼ Сохранение JWT
~/.aizoomdoc/config.json
    │
    ├─ access_token: "eyJ..."
    ├─ expires_at: 2026-01-19T12:00:00
    └─ refresh_needed: false
    │
    ▼
Последующие запросы
    ├─ Проверка expires_at
    │  ├─ Если истекает < 5 мин
    │  │  └─ Автоматический refresh (POST /auth/exchange)
    │  │     └─ Новый токен сохраняется
    │  └─ Если актуален
    │     └─ Отправка запроса с текущим токеном
    │
    ├─ Authorization: Bearer eyJ...
    │
    └─ Сервер получает, валидирует JWT (core/auth.py)
```

---

## 🔐 Безопасность

### Аутентификация

1. **Первый уровень**: Статичный токен (Static Token)
   - Хранится на сервере в переменной окружения `STATIC_TOKEN`
   - Используется для обмена на JWT

2. **Второй уровень**: JWT (JSON Web Token)
   - Генерируется после валидации статичного токена
   - TTL: 1 час (настраивается в `config.py`)
   - Автоматический refresh на клиенте

### Авторизация

- **JWT валидация** на каждом защищённом endpoint (кроме `/health`, `/docs`)
- **Scopes** (планируется): admin, user, guest
- **CORS**: Настраивается через `CORS_ORIGINS` в `.env`

### Хранилище данных

- **Supabase**: Использует PostgreSQL с RLS (Row-Level Security)
- **S3/R2**: Приватные bucket'ы с IAM политиками
- **Переменные окружения**: `.env` файл не коммитится в репозиторий

### Транспортная безопасность

- **HTTPS в продакшене** (рекомендуется)
- **WebSocket WSS** (для стриминга)
- **Content-Type validation**: Multipart для файлов

---

## 📈 Масштабирование

### Горизонтальное масштабирование

```
Load Balancer (nginx/Traefik)
    ├─ FastAPI Server 1 (port 8001)
    ├─ FastAPI Server 2 (port 8002)
    └─ FastAPI Server 3 (port 8003)
    
    └─ Shared Database (Supabase)
    └─ Shared Storage (S3/R2)
```

**QueueService**: Каждый сервер имеет локальную очередь с настройками:
- `queue_max_concurrent`: Макс параллельных запросов (default: 2)
- `queue_max_size`: Макс размер очереди (default: 100)

### Вертикальное масштабирование

- **Uvicorn workers**: Увеличение в продакшене
- **Supabase replicas**: Для read-heavy операций
- **S3/R2 CDN**: Для раздачи больших файлов

### Кэширование

1. **RenderCache**: Кэширование рендерированных блоков
2. **Database connection pooling**: Через Supabase SDK
3. **HTTP кэширование**: ETag, If-Modified-Since

### Мониторинг

- **Логирование**: Структурированные логи в `logs/`
- **Health endpoint**: `GET /health` для load balancer'а
- **LLM диалоги**: Логирование в `logs/llm_dialog_*.log`

---

## 🔄 Модели данных

### Пользователь (User)

```json
{
  "id": "uuid",
  "username": "admin",
  "status": "active",
  "created_at": "2026-01-13T00:00:00Z",
  "updated_at": "2026-01-19T12:00:00Z"
}
```

### Чат (Chat)

```json
{
  "id": "uuid",
  "user_id": "uuid",
  "title": "Анализ вентиляции",
  "description": "Вопросы по системе В2",
  "created_at": "2026-01-19T12:00:00Z",
  "updated_at": "2026-01-19T12:00:00Z"
}
```

### Сообщение (ChatMessage)

```json
{
  "id": "uuid",
  "chat_id": "uuid",
  "user_id": "uuid",
  "role": "user|assistant",
  "content": "Какое оборудование используется?",
  "attached_files": ["uuid1", "uuid2"],
  "created_at": "2026-01-19T12:00:00Z"
}
```

### Настройки (Settings)

```json
{
  "user_id": "uuid",
  "model_profile": "simple|complex",
  "selected_role_prompt_id": "uuid|null",
  "gemini_api_key": "AIza...",
  "updated_at": "2026-01-19T12:00:00Z"
}
```

### Событие стриминга (StreamEvent)

```json
{
  "event": "phase_started|llm_token|phase_completed|completed|error",
  "data": {
    "phase": "search|processing|llm",
    "token": "Hello",
    "description": "Поиск контекста в документах"
  }
}
```

---

## 📝 Примеры использования

### 1. Полный цикл через CLI

```bash
# Авторизация
aizoomdoc login --token ABC123

# Проверка
aizoomdoc user me

# Создание чата
aizoomdoc chat new "Анализ ОВ системы"

# Отправка вопроса (автоматически используется последний чат)
aizoomdoc chat send "Какие параметры указаны в спецификации?"

# История
aizoomdoc chat history

# Загрузка файла
aizoomdoc file upload document.pdf

# Повторный запрос с файлом
aizoomdoc chat send "Проанализируй этот документ"

# Смена режима
aizoomdoc settings set-model complex

# Выбор роли
aizoomdoc settings set-role "Инженер"

# Выход
aizoomdoc logout
```

### 2. Использование как библиотеки

```python
from aizoomdoc_client import AIZoomDocClient

# Создание клиента
client = AIZoomDocClient(
    server_url="http://localhost:8000",
    static_token="ABC123"
)

# Авторизация
user = await client.authenticate()
print(f"Добро пожаловать, {user.username}!")

# Создание чата
chat = await client.create_chat(title="Моя папка анализа")

# Отправка вопроса со стримингом
print("Ответ: ", end="", flush=True)
async for event in client.send_message(chat.id, "Что такое ОВ?"):
    if event.event == "llm_token":
        print(event.data["token"], end="", flush=True)
    elif event.event == "phase_started":
        print(f"\n[{event.data['phase']}]", end="")

print()  # Новая строка

# Загрузка файла
file_info = await client.upload_file("technical_spec.pdf")

# Использование файла в вопросе
async for event in client.send_message(
    chat.id, 
    "Проанализируй спецификацию",
    attached_file_ids=[file_info.id]
):
    # Обработка событий...
    pass

# Получение истории
messages = await client.get_chat_history(chat.id)
for msg in messages:
    role = "You" if msg.role == "user" else "AI"
    print(f"{role}: {msg.content}")

# Смена настроек
await client.update_settings(model_profile="complex")
```

---

## 🚀 Развертывание

### Docker

```bash
# Сборка образа
docker build -t aizoomdoc-server:2.0.0 .

# Запуск контейнера
docker run -p 8000:8000 \
  -e JWT_SECRET_KEY="your-secret" \
  -e SUPABASE_URL="https://..." \
  -e SUPABASE_SERVICE_KEY="..." \
  -e R2_ENDPOINT_URL="..." \
  -e R2_ACCESS_KEY_ID="..." \
  -e R2_SECRET_ACCESS_KEY="..." \
  aizoomdoc-server:2.0.0
```

### Docker Compose

```bash
docker-compose up -d
```

Смотрите `docker-compose.yml` для полной конфигурации.

---

## 📚 Дополнительные ресурсы

- [README Server](../README.md) — Подробно о сервере
- [README Client](../aizoomdoc-client-py/README.md) — Подробно о клиенте
- [IMPLEMENTATION_PLAN](./IMPLEMENTATION_PLAN_BLOCK_INDEX_CACHE_BUDGET.md) — План реализации
- [CLIENT_INTEGRATION](../CLIENT_INTEGRATION.md) — Интеграция компонентов
- [VPS_DEPLOYMENT](./VPS_DEPLOYMENT_UBUNTU.md) — Развертывание на VPS

---

## 📞 Поддержка

По вопросам архитектуры или разработки обратитесь к техлиду проекта.

---

**Последнее обновление**: 2026-01-19  
**Версия документа**: 2.0.0

