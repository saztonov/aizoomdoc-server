План клиент‑серверной версии
Цели и границы
Backend остаётся на Python и содержит всю бизнес‑логику (поиск, обработка PDF/картинок, zoom, вызовы LLM Gemini, интеграции Supabase/S3).
Клиенты: локальный Python (делаем сразу) и веб React (репозиторий создаём сразу, реализацию откладываем). Оба общаются только с backend.
Auth: статичный токен → обмен на access+refresh JWT (/auth/exchange). TTL access: например 60 минут.
Gemini API key: per-user, хранится в Supabase в зашифрованном виде; расшифровка и использование только на backend; ключи пользователей не хранятся в env.
Архитектура (в общих чертах)
HTTP
HTTP_WS
read_write
upload_download
GeminiAPI
PythonClient
PythonAPI
ReactClient
SupabaseDB
S3Storage
GoogleAIStudio


1) Репозитории и ответственность
Repo 1: aizoomdoc-server
API (HTTP + WebSocket/SSE)
Сервисы: Search, Image/PDF, LLM, ProjectsTree
Адаптеры: Supabase, S3
Auth (StaticToken→JWT: access+refresh)
Repo 2: aizoomdoc-client-py
Полнофункциональный локальный клиент (как минимум CLI; UI по необходимости)
Хранение access/refresh JWT; рефреш по refresh; exchange по StaticToken только при первом входе/после отзыва
Repo 3: aizoomdoc-client-web
React UI (позже): чат, дерево проектов, просмотр файлов/кропов
WebSocket/SSE для стриминга ответа (закладываем в сервере сразу)
2) Модель пользователей и секретов
2.1 StaticToken
Храним хэш статичного токена в таблице users (вместе с username и статусом).
На /auth/exchange сервер принимает StaticToken, хэширует и ищет пользователя по users.static_token_hash.
2.2 JWT
Сервер выпускает access JWT (TTL, например 60 минут) и refresh token (дольше; TTL согласуем отдельно).
Минимальные claims: sub(user_id), token_id, exp, scopes (+ при необходимости jti).
Поток:\n+  - POST /auth/exchange (StaticToken) → {access_token, refresh_token, expires_in, user}`\n+  - `POST /auth/refresh (refresh_token) → новый access_token (+ опционально rotation refresh)\n+  - POST /auth/logout → отзыв refresh (серверная инвалидизация)\n+- Для безопасности refresh должны быть серверно-инвалидируемыми (храним хэш refresh в Supabase, поддерживаем отзыв/ротацию).
2.3 Шифрование Gemini API key (без кода, но с решением)
Пояснение “где хранить ключи Gemini”:\n+- Per-user Gemini API key хранится в Supabase, потому что он привязан к конкретному пользователю/статичному токену.\n+- В env сервера хранятся только:\n+  - ключ(и) подписи JWT**\n+  - **master key для шифрования (или доступ к нему через секрет-хранилище деплоя)\n+  - прочие системные секреты (S3, Supabase service key и т.п.)\n+- Ключи пользователей не кладём в env, иначе это не масштабируется и усложняет управление/ротацию.\n+\n+Безопасный дефолт (без кода): шифрование на уровне приложения.

В Supabase хранить: ciphertext, key_version, updated_at.
На сервере master key хранить в env (и/или в секрет-хранилище деплоя), поддержать ротацию по key_version.
Опционально: кешировать расшифрованный ключ в памяти на короткое время (меньше TTL JWT) для уменьшения запросов в БД.
2.4 Изменения в Supabase под многопользовательскую модель (минимум)
Цель: поддержать многопользовательский режим и централизованное управление настройками/промптами.\n+\n+Фиксируем правила:Пользовательские настройки (user-editable):

Выбор профиля модели (model_profile): simple (flash) | complex (flash+pro)
Выбор роли (selected_role_prompt_id): NULL (без роли) | id промпта-роли из prompts_user
Все остальные настройки и системные промпты управляются только админами.Структура таблиц Supabase (концептуально; DDL напишем при реализации):

users: id, username, static_token_hash, status, created_at, last_seen_at.
auth_refresh_tokens: refresh_token_hash, user_id, status, expires_at, revoked_at.
user_secrets: зашифрованный gemini_api_key_ciphertext + key_version на пользователя.
settings_system: системные дефолты/ограничения (редактируют только админы).
settings_user: user_id, model_profile (simple/complex), selected_role_prompt_id (nullable FK на prompts_user).
Промпты в БД:

prompts_system: системные промпты (например llm_system, json_annotation, html_ocr, flash_extractor). Управляются только админами. Исполняются всегда (набор зависит от логики: Flash/Pro, наличие JSON/HTML).
prompts_user: роли (например "инженер", "экономист", "инженер по гарантии"). Создаются/редактируются только админами, но выбираются пользователем. Если пользователь выбрал роль, её промпт добавляется после системных в запросе.
Для обоих: id, name, content, is_active, version, updated_at, description (опционально).
Политика применения промптов в рантайме:

Системные промпты (prompts_system) применяются всегда (исполнение определяется серверной логикой: параметры model_profile, наличие JSON/HTML файлов, режим Flash/Pro).
Пользовательская роль (prompts_user): если settings_user.selected_role_prompt_id не NULL, промпт этой роли добавляется после системных в запросе к LLM (позиция: перед или после системных — уточним по необходимости; вероятно, сразу после first system message).
Таким образом, финальный промпт = системные (обязательные) + роль (опциональная, если выбрана пользователем).
2.5 Текущая система промптов (что есть сейчас) и порядок применения
Какие “4 промпта” реально участвуют в работе
Функционально в текущем приложении используются 4 промпта из data/:

llm_system_prompt.txt — базовая системная инструкция анализа (в т.ч. про ZOOM и точность шифров).
json_annotation_prompt.txt — правила интерпретации JSON-аннотаций (как искать блоки и как вызывать request_images/zoom).
html_ocr_prompt.txt — правила интерпретации HTML OCR (как читать текст/таблицы и связывать с image-блоками).
flash_extractor_prompt.txt — промпт 1‑го этапа в режиме Flash+Pro (сбор контекста, запросы изображений/зумов).
УСТАРЕВШЕЕ (удаляем):

selection_prompt.txt и метод select_relevant_images в src/llm_client.py — фактически не используются. При переносе не включаем в систему промптов и удаляем метод.
Порядок применения сейчас (два режима)
1) Обычный (не Flash+Pro) режим в GUI:

Собирается "композитный" system prompt в порядке:
(опционально) пользовательская роль из UI (user_prompt)
llm_system_prompt.txt
json_annotation_prompt.txt
html_ocr_prompt.txt
служебная приписка про отключение нативных tools
2) Режим Flash+Pro:

Этап 1 (Flash): system = flash_extractor_prompt.txt, user message = текст документа + каталог изображений + запрос; затем итерации request_images/zoom.
Этап 2 (Pro): system основан на llm_system_prompt.txt (и формате ответа), user message = релевантные текстовые блоки + прикреплённые изображения/зумы.
Нюанс: на этапе Pro сейчас не добавляются json_annotation_prompt.txt и html_ocr_prompt.txt — при переносе лучше это выровнять.
Порядок применения в client-server (предлагаемый)
Simple (flash) режим:

Роль (если выбрана пользователем): `prompts_user[selected_role_prompt_id]`
Системные промпты (все активные для этого режима): llm_system + json_annotation + html_ocr
User message (запрос + контекст)
Complex (flash+pro) режим:

Этап 1 (Flash):
System: flash_extractor
User: документ + каталог изображений + запрос
Итерации: request_images/zoom
Этап 2 (Pro):
Роль (если выбрана)
Системные: llm_system + json_annotation + html_ocr (выровнять с simple!)
User: релевантные блоки + изображения/зумы
Анализ: нужны ли json_annotation и html_ocr промпты
Что они делают:

json_annotation_prompt.txt (~60 строк): объясняет LLM формат JSON-блоков (block_id, group_name, zone_name, crop_url), как запрашивать изображения через request_images, как использовать zoom.
html_ocr_prompt.txt (~55 строк): объясняет формат HTML-блоков (ID, тип, страница), как читать текст/таблицы и связывать с изображениями.
Это не настройки парсеров! Это контекстные инструкции для LLM, специфичные для формата данных.Рекомендация:

Вариант А (рекомендую): оставить модульными — так проще обновлять при изменении формата данных, не трогая llm_system.
Вариант Б: встроить в llm_system_prompt — получится один большой промпт (~250 строк), но проще управлять версиями.
Для первой версии: оставляем модульными (3 системных промпта: llm_system, json_annotation, html_ocr + отдельно flash_extractor). Позже можно объединить, если понадобится.

Итоговая структура промптов для client-server
Системные промпты (prompts_system):

llm_system — базовая инструкция анализа (ZOOM, шифры, квадранты)
json_annotation — правила работы с JSON-блоками
html_ocr — правила работы с HTML-блоками
flash_extractor — промпт для этапа Flash (только в complex режиме)
Пользовательские промпты (prompts_user):

Роли (инженер, экономист и т.п.) — создаются админами, выбираются пользователем
Удаляем:

selection_prompt.txt и метод select_relevant_images() — не используются
Порядок применения:

Simple: роль (если выбрана) → llm_system + json_annotation + html_ocr → user message
Complex Flash: flash_extractor → user message (итерации)
Complex Pro: роль (если выбрана) → llm_system + json_annotation + html_ocr → user message
3) API контракт (минимальный набор)
Auth
POST /auth/exchange → {access_token, refresh_token, expires_in, user}
POST /auth/refresh → {access_token, expires_in} (+ опционально новый refresh)
POST /auth/logout → отзыв refresh токена
Chat
POST /chats (создать)
GET /chats/{chat_id} (история)
POST /chats/{chat_id}/messages (сообщение пользователя)
WS /chats/{chat_id}/stream или GET /chats/{chat_id}/stream (SSE) для токенов LLM + событий пайплайна
Projects tree
GET /projects/tree (узлы)
GET /projects/documents/{doc_id}/results (файлы результатов)
Files
POST /files/upload (загрузка)
GET /files/{file_id} (метаданные/ссылка)
Важно: клиенты никогда не получают Gemini ключ.

4) Пайплайн обработки запроса (перенос текущей логики)
Ориентир на текущее поведение в src/main.py:

Search → сбор контекста → LLM ответ → (если есть zoom_requests) → ImageProcessor → повторное сообщение → финальный ответ.
Перенос в server:

Вынести эти шаги в оркестратор “agent run”, который пишет события (progress) и сохраняет сообщения/изображения через Supabase/S3.
5) Стриминг и фоновые задачи
Для UX чата: стримить токены Gemini и события пайплайна (найдено N блоков, скачан PDF, сформированы crops, выполнен zoom).
Тяжёлые операции (скачивание/кропы/зумы) выполнять асинхронно; на первом этапе можно без очереди, затем добавить полноценные job’ы.
6) Клиенты
6.1 Python client
Команды: login(exhange), chat send, subscribe stream.
Хранить access+refresh; при истечении access → делать refresh; exchange по StaticToken только при первом входе/после отзыва.
6.2 Web React
Хранить JWT в памяти/secure storage (зависит от выбранной модели), подключить WS/SSE.
UI: чат + дерево проектов + просмотр PDF/изображений + выбор результата/приложения к чату.
7) План внедрения по этапам (чтобы быстрее получить работающий MVP)
7) Этапы реализации (без MVP-упрощений; web позже)
Этап A (Server foundation): репозиторий aizoomdoc-server, полный Auth (exchange+refresh+logout), базовые модели, интеграция Supabase/S3, каркас событий/стриминга.
Этап B (Full agent pipeline): полный пайплайн Search→Gemini→Zoom (с сохранением истории, файлов и событий), стриминг токенов и статусов.
Этап C (Projects/Tree): эндпоинты дерева проектов и результатов документов, прикрепление документов/результатов к чатам.
Этап D (Python client full): репозиторий aizoomdoc-client-py, полноценная работа с чатами/стримингом/деревом/файлами, устойчивость к refresh/отзыву.
Этап E (Supabase schema hardening): окончательно утвердить и внедрить таблицы users/tokens/secrets/settings, правила редактирования настроек, ротации ключей.
Этап F (Web later): репозиторий aizoomdoc-client-web уже создан; реализацию UI начать после стабилизации server+py-client.