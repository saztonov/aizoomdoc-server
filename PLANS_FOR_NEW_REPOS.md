# Планы для новых репозиториев AIZoomDoc v2

Назначение: этот файл — “источник истины” для реализации. Скопируйте соответствующий раздел в каждый новый репозиторий как `PLAN.md` (или оставьте как есть и используйте как ТЗ).

Ограничения (общее):
- Старый проект **не менять**.
- Ответы/логика/тексты ориентированы на русскоязычный интерфейс.
- В миграциях/запросах **не включать RLS**.
- Пользовательские ключи Gemini **не хранить в env**. В env — только системные секреты (JWT signing, master key шифрования, Supabase/S3 и т.п.).

---

## PLAN: `aizoomdoc-server`

### 0) Цель и итоговый результат
Сделать полнофункциональный Python backend (HTTP + стриминг) для клиент-серверной версии AIZoomDoc:
- Auth по статичному токену → выдача `access_jwt + refresh_jwt`
- Вызовы Gemini с **per-user** ключами
- Пайплайн анализа документов: `simple` (flash) и `complex` (flash+pro, Flash собирает контекст, Pro отвечает)
- Работа с файлами (загрузка/хранение/выдача ссылок), `request_images`/`zoom`
- Хранение чатов/сообщений/файлов/настроек/промптов в Supabase
- Настройки пользователя: только `model_profile` и `role`
- Промпты в БД: системные + роли

### 1) Основные принципы архитектуры
- **Клиент не ходит в Supabase/S3 напрямую**, только через server API.
- **Все секреты** (Gemini user keys, Supabase service key, S3 creds) используются только на сервере.
- Сервер реализует **событийную модель** для стриминга:
  - токены ответа LLM
  - этапы пайплайна (поиск, скачивание, кропы, зумы, вызовы Flash/Pro)
  - ошибки/варнинги (например не удалось скачать файл)

### 2) Auth модель (StaticToken → JWT)
#### 2.1 Требования
- Клиент хранит **StaticToken** (вручную заданный секрет).
- Сервер принимает StaticToken на `/auth/exchange` и выдает:
  - `access_jwt` (TTL 60 минут)
  - `refresh_jwt` (TTL больше; например 30 дней — параметризуемо)
- Refresh должен быть **отзываемым**: сервер хранит **хэш refresh** и может инвалидировать.

#### 2.2 Эндпоинты auth (контракт)
- `POST /auth/exchange` — вход по StaticToken
  - input: `{static_token}`
  - output: `{access_token, refresh_token, expires_in, user}`
- `POST /auth/refresh` — обновление access по refresh
  - input: `{refresh_token}`
  - output: `{access_token, expires_in}` (+ опционально rotate refresh)
- `POST /auth/logout` — отзыв refresh
  - input: `{refresh_token}` или implicit по текущей сессии
  - output: `{ok}`

#### 2.3 Хранение пользователей и токенов (концептуально; DDL потом)
- `users`: `id`, `username`, `static_token_hash`, `status`, `created_at`, `last_seen_at`
- `auth_refresh_tokens`: `id`, `user_id`, `refresh_token_hash`, `status`, `expires_at`, `revoked_at`, `created_at`, `last_used_at`

Примечания:
- `static_token_hash` и `refresh_token_hash` — хранить хэш, не исходный токен.
- Нужен механизм “revoked” без RLS (админская логика на сервере).

### 3) Per-user Gemini API key: хранение и использование
#### 3.1 Где хранится
- В Supabase, таблица `user_secrets`:
  - `user_id`
  - `gemini_api_key_ciphertext`
  - `key_version`
  - `updated_at`

#### 3.2 Что в env сервера
- `JWT_SIGNING_KEY` (или пара ключей)
- `MASTER_ENCRYPTION_KEY` (ключ шифрования для `user_secrets`)
- Supabase URL + service/anon ключи, S3 creds, прочие системные секреты

#### 3.3 Правило выбора ключа
- Любой вызов LLM выполняется **от имени текущего пользователя**:
  - сервер берёт `user_id` из `access_jwt`
  - расшифровывает `gemini_api_key` пользователя
  - создаёт Gemini клиент с этим ключом

### 4) Настройки (system vs user)
#### 4.1 User-editable настройки
Только:
- `model_profile`: `simple` (flash) | `complex` (flash+pro)
- `selected_role_prompt_id`: NULL (“без роли”) | id роли из `prompts_user`

Таблица (концептуально):
- `settings_user`: `user_id`, `model_profile`, `selected_role_prompt_id`

#### 4.2 System настройки (админские)
Всё остальное:
- лимиты, список разрешённых моделей, параметры пайплайна, дефолты
- таблица: `settings_system`

### 5) Промпты в БД и порядок применения
#### 5.1 Системные промпты (`prompts_system`)
Сохраняем 4 промпта как отдельные сущности (версионирование/активация/rollback):
- `llm_system` — базовая стратегия (ZOOM, шифры, квадранты)
- `json_annotation` — как LLM понимает JSON-блоки/ID и вызывает `request_images`/`zoom`
- `html_ocr` — как LLM понимает HTML OCR блоки/таблицы
- `flash_extractor` — промпт этапа Flash (только в complex)

Таблица: `prompts_system`: `id`, `name`, `content`, `version`, `is_active`, `updated_at`, `description`

#### 5.2 Пользовательские промпты-Роли (`prompts_user`)
- Роли: “инженер”, “экономист”, “инженер по гарантии”, …
- Создаются/редактируются админами, **выбираются пользователем** через `settings_user.selected_role_prompt_id`.
- Добавляются в запрос **после** системных промптов (или в начало композита — выбрать один подход и зафиксировать; важнее стабильность).

Таблица: `prompts_user`: `id`, `name`, `content`, `version`, `is_active`, `updated_at`, `description`

#### 5.3 Удаляем устаревшее
- `selection_prompt.txt` и метод `select_relevant_images()` — **не переносить**, удалить при переносе логики в server.

#### 5.4 Порядок применения промптов (фиксируем)
- **Simple (flash)**:
  1) (опционально) Role prompt (`prompts_user[selected_role_prompt_id]`)
  2) `llm_system` + `json_annotation` + `html_ocr`
  3) User message: запрос + контекст + изображения (если есть)
- **Complex (flash+pro)**:
  - Flash этап:
    1) system: `flash_extractor`
    2) user: документ + каталог изображений + запрос
    3) итерации: `request_images`/`zoom`
  - Pro этап:
    1) (опционально) Role
    2) `llm_system` + `json_annotation` + `html_ocr`
    3) user: релевантные текстовые блоки + изображения/зумы

### 6) API (минимальный, но полнофункциональный контракт)
#### 6.1 Settings/Prompts
- `GET /me` — текущий пользователь и effective settings
- `PATCH /me/settings` — менять только `model_profile` и `selected_role_prompt_id`
- `GET /prompts/roles` — список доступных ролей (active)

Админские (можно закрыть отдельным админ-токеном/внутренним доступом):
- `GET/POST/PATCH /admin/prompts/system`
- `GET/POST/PATCH /admin/prompts/roles`
- `GET/PATCH /admin/settings/system`

#### 6.2 Chats
- `POST /chats` — создать чат
- `GET /chats/{chat_id}` — история
- `POST /chats/{chat_id}/messages` — отправить сообщение пользователя и запустить пайплайн

Стриминг (выбрать один протокол и придерживаться):
- `GET /chats/{chat_id}/stream` (SSE) **или** `WS /chats/{chat_id}/stream` (WebSocket)
  - события: `phase_started`, `phase_progress`, `llm_token`, `llm_final`, `error`

#### 6.3 Files/Documents
- `POST /files/upload` — загрузить файл (pdf/md/html/json)
- `GET /files/{file_id}` — метаданные/ссылка на скачивание
- `POST /chats/{chat_id}/attachments` — прикрепить файл к чату

#### 6.4 Projects tree (если нужно сразу, иначе вторым этапом)
- `GET /projects/tree`
- `GET /projects/documents/{doc_id}/results`

### 7) Пайплайн обработки (логика)
#### 7.1 Simple (flash)
- Подготовить контекст (из поиска/индекса, из прикрепленных файлов, из Supabase)
- Применить промпты
- Вызвать Flash модель
- Если модель просит `request_images`/`zoom`:
  - выполнить, прикрепить результаты, продолжить
- Вернуть финальный ответ

#### 7.2 Complex (flash+pro)
- Flash этап: собрать релевантные блоки + запросить нужные картинки/зумы
- Pro этап: сформировать “контекст для ответа” и вызвать Pro
- Вернуть финальный ответ

### 8) Модель данных для чатов и файлов (концептуально)
Минимум:
- `chats` (user_id, title, created_at)
- `chat_messages` (chat_id, role, content, created_at)
- `chat_message_images` / `files` / `message_attachments` (в зависимости от текущей схемы)

Цель: сохранить историю, ссылки на S3/Google Files URI, типы вложений (viewport/zoom/original).

### 9) Критерии готовности (Definition of Done)
Сервер считается “готовым к использованию python-клиентом”, когда:
- Auth: exchange/refresh/logout работают, refresh отзывается, access TTL = 60 мин.
- Настройки: `model_profile` и `role` сохраняются и влияют на выбор модели/промпта.
- Prompts: системные промпты читаются из БД, роли из БД, есть активная версия.
- Чаты: создаются, сообщения сохраняются, пайплайн запускается.
- Стриминг: клиент может получать прогресс и финальный ответ.
- Файлы: можно загрузить PDF и прикрепить к чату; сервер может использовать файл в пайплайне.

---

## PLAN: `aizoomdoc-client-py`

### 0) Цель
Сделать локальный Python-клиент для работы с `aizoomdoc-server`:
- логин по StaticToken
- хранение access/refresh и автоматический refresh
- работа с чатами и стримингом
- управление user settings: модель (simple/complex) и роль (или “без роли”)

### 1) UX/команды (CLI)
Рекомендуемый набор команд:
- `login` (ввод static token, сохранение токенов)
- `me` (показать текущие настройки и пользователя)
- `settings set-model simple|complex`
- `settings set-role none|<role_name_or_id>`
- `chat new "<title>"` (или auto-title)
- `chat use <chat_id>`
- `chat send "<message>"` (с live-стримингом)
- `chat history [--tail N]`
- `file upload <path>`
- `chat attach <file_id>`
- `logout`

### 2) Хранение токенов
- Локальный файл (например в профиле пользователя) с:
  - server_url
  - access_token + exp
  - refresh_token
  - active_chat_id (опционально)

### 3) Поведение refresh
- При 401/exp: автоматически вызвать `/auth/refresh` и повторить запрос.
- При неуспешном refresh: требовать повторный `login` (exchange).

### 4) Стриминг
- Поддержать выбранный на сервере механизм (SSE или WS).
- В CLI отображать:
  - этапы пайплайна
  - потоковые токены ответа
  - финальный markdown/итог

### 5) Критерии готовности
Клиент считается “готовым”, когда:
- Стабильно логинится, переживает истечение access, делает refresh автоматически.
- Умеет менять `model_profile` и роль, и видно что это влияет (например по названию модели в ответе/логах).
- Умеет отправить сообщение и получить стримингом финальный ответ.
- Умеет загрузить файл и прикрепить к чату.

---

## PLAN: `aizoomdoc-client-web` (делать позже)

### Статус
Репозиторий создан. Реализацию начинать после стабилизации server и client-py.

### Цель (когда начнем)
React UI:
- логин по StaticToken → access/refresh
- чат + история
- настройки: model_profile, role
- стриминг ответа
- дерево проектов + просмотр результатов (позже)

### Минимальный UX (когда начнем)
- Экран логина (static token)
- Основной экран: чат + панель настроек (model/role)
- Просмотр вложений/изображений/zoom


