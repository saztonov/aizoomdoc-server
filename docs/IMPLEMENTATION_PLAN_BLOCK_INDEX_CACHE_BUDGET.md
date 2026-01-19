# План внедрения: BlockIndex/BlockIndexer + LRU/версионный кеш рендеров + Token budgeting (AIZoomDoc Server)

Дата: 2026-01-19  
Область: **только клиент‑серверная архитектура** (AIZoomDoc Server + Supabase Chat DB + Projects DB как read-only источник)  
Ключевое ограничение: **индекс хранить только в Supabase Chat DB**

---

## Цели

- **Ускорить и стабилизировать** анализ чертежей за счёт кеширования рендеров (PDF→PNG/ROI) с лимитами по диску.
- **Снизить промахи выбора листов** и количество итераций follow-up (ROI/картинки) за счёт BlockIndex (семантический каталог блоков).
- **Стабилизировать качество/стоимость** вызовов Gemini за счёт token budgeting и учёта media-бюджета.

---

## Термины и текущая реальность кода

- **Projects DB**: read-only источник дерева/файлов (`tree_nodes`, `node_files`, кропы). Клиент: `app/db/supabase_projects_client.py`.
- **Chat DB**: основная БД сервера (users/chats/settings/prompts/files). Клиент: `app/db/supabase_client.py`.
- **Evidence (рендер)**: фактически реализован через `app/services/evidence_service.py` (preview/quadrants/roi), а не через `image_service.py`.
- **Agent**: оркестрация в `app/services/agent_service.py` (итерации, материалы, followup images/rois).
- **LLM**: `app/services/llm_service.py` использует `response_schema` (строгий JSON) для Flash collector и Answer.

---

## 1) LRU/версионный кеш рендеров (PDF→PNG/ROI)

### Что есть сейчас

- `EvidenceService.render_pdf_page()` кеширует на диск по ключу `hash(cache_key:page:dpi)`.
- **Нет**:
  - версионности (кеш не инвалидируется при обновлении источника),
  - лимита размера кеша,
  - LRU-эвикшена/TTL.

### Цель улучшения

- Сделать кеш:
  - **версионным** (не использовать старые PNG при обновлении crop-PDF),
  - **ограниченным** (max MB/GB),
  - **LRU** (вытеснять наименее используемое),
  - опционально **TTL** (самоочистка по времени).

### Предлагаемая схема ключей и версий

- Источник: crop-PDF (обычно по `r2_key` из Projects DB, скачиваем через `S3Client`).
- Версия источника (приоритет):
  - `etag` / `last_modified` из `S3 HEAD` по `r2_key`,
  - fallback: `sha256(pdf_bytes)` (дороже CPU, но всегда возможно).
- Ключ кеша рендера страницы:
  - `(source_id=r2_key_or_url, source_version, page, dpi)`.
- Ключ кеша ROI:
  - `(source_id, source_version, page, dpi, bbox_norm_rounded)`; bbox округлять (например 4 знака).

### Как хранить LRU-метаданные

Минимально:
- `path`, `size_bytes`, `last_access_at`, `created_at`, `source_version`.

Варианты:
- **sqlite** (предпочтительно): атомарность, быстрые выборки/эвикшен.
- json sidecar (MVP): проще, но менее надёжно при конкурентном доступе.

### Настройки (в `app/config.py`)

Предложение:
- `EVIDENCE_CACHE_DIR` (опционально)
- `EVIDENCE_CACHE_MAX_MB` (например 2000)
- `EVIDENCE_CACHE_TTL_DAYS` (например 14)
- `EVIDENCE_CACHE_ENABLE` (true/false)

---

## 2) BlockIndexer / BlockIndex (только Supabase Chat DB)

### Что это

- **BlockIndex**: серверный “каталог” графических блоков документа, где каждый `block_id` описан структурированно:
  - что изображено, дисциплина, ключевые слова, (опционально) шифры систем, этаж/разрез/масштаб, качество/уверенность.
- **BlockIndexer**: пайплайн, который строит/обновляет записи индекса.
  - Технические библиотеки (PyMuPDF/PIL) здесь не “понимают” чертёж, они лишь помогают подготовить входные данные.
  - “Смысл” (title/keywords/discipline/…) извлекает **LLM (Gemini Flash)** в строгом JSON по schema.

### Зачем

- Быстрый и стабильный “candidate selection” по документу:
  - вместо случайного выбора листов моделью,
  - меньше дорогих итераций ROI/картинок,
  - лучше recall по шифрам систем (важно: В2 ≠ В21).

### Где хранить индекс (строгое решение)

- **Только Supabase Chat DB**.
- Projects DB остаётся read-only источником кропов/дерева.

Причины выбора Chat DB:
- запись гарантированно доступна (service key),
- проще мигрировать/версионировать схему,
- меньше связанность с внешним проектом Projects DB.

### Минимальная модель данных (Chat DB)

Одна таблица `document_block_index` (MVP) или две (если нужен прогресс задач).

Рекомендуемая `document_block_index`:
- `id uuid`
- `document_id uuid` (ID узла документа из Projects DB: `tree_nodes.id`)
- `block_id text`
- `crop_r2_key text` (источник crop-PDF)
- `source_version text` (etag/last_modified/хеш)
- `title text`
- `discipline text`
- `keywords jsonb` (array of strings)
- `what_is_on_drawing text`
- `floor_or_section text null`
- `scale text null`
- `system_codes jsonb null` (array; точные шифры: "ОВ1.2", "В2" и т.п.)
- `status text` (например: `pending|indexed|failed`)
- `attempt_count int`
- `last_error text null`
- `prompt_version int`
- `model_name text`
- `indexed_at timestamptz`
- `created_at/updated_at`

Индексы:
- уникальный `(document_id, block_id)`
- индекс по `document_id`
- опционально: GIN по `keywords` и `system_codes`
- опционально: trigram/ILIKE по `title` и `what_is_on_drawing` (если потребуется полнотекст/поиск)

### Пайплайн индексации (сервер)

#### Шаг 0 — Триггеры

Индексация **не обязана** блокировать ответ пользователю.

Режимы:
- **On-demand (рекомендуемый по умолчанию)**: при первом запросе к документу проверяем индекс → если нет/устарел, ставим индексацию в очередь.
- **Прогрев (опционально)**: админ/джоба запускает индексацию заранее для “горячих” документов.
- **Refresh**: переиндексация только при изменении `source_version`, смене `prompt_version`, или по TTL.

#### Шаг 1 — Discovery (сбор входных данных)

Для `document_id`:
- получить `node_files` результатов: `annotation`, `result_md`, `ocr_html`, `crop`.
- получить список crop-PDF:
  - `block_id` (из имени файла / r2_key),
  - `crop_r2_key`,
  - `source_version` (из S3 HEAD).

#### Шаг 2 — Indexing (LLM Flash)

Для каждого crop-PDF (или батчами):
- подать в Flash **сам crop-PDF** (через File API или напрямую по URI, как поддерживает текущая интеграция),
- попросить строгий JSON по schema:
  - `title`, `keywords[]`, `discipline`, `what_is_on_drawing`,
  - опционально `scale`, `floor_or_section`,
  - желательно `system_codes[]` (точные шифры).

#### Шаг 3 — Persist (Chat DB upsert)

Upsert по `(document_id, block_id)`:
- если `source_version` совпадает и `prompt_version` тот же → пропустить,
- если отличается → обновить запись.

#### Шаг 4 — Использование индекса в ответном пайплайне

При вопросе пользователя по `document_id`:
- быстро выбрать кандидаты блоков:
  - по `system_codes` (точный матч),
  - по keywords/title/what_is_on_drawing.
- варианты применения:
  - **Soft guidance**: подсказать Flash collector’у “рекомендуемые блоки”.
  - **Hard prefetch**: заранее запросить `requested_images` для top-K (ограничить K).

Важно: индекс не является доказательством. Факты подтверждаются через:
- selected_blocks (MD/HTML),
- evidence PNG (overview/quadrants/roi).

### Параллельность с интерактивными запросами

Да, потенциально это **два независимых потока вызовов LLM**:
- интерактивный ответ на вопрос,
- фоновая индексация.

Но **не обязательно** запускать их одновременно. Нужны лимиты:
- приоритет интерактива над индексатором,
- ограничение concurrency для индексации (например 1 воркер на сервер),
- backoff/retry при rate limit.

---

## 3) Token budgeting и учёт media budget

### Проблема

- При длинном `full_context` + росте `materials_json` + множестве PNG LLM начинает:
  - “терять” важные части контекста,
  - требовать лишние итерации,
  - быть нестабильной по качеству.

### Цель

Стабилизировать:
- сколько текста отправляем,
- сколько изображений/ROI отправляем,
- как растёт `materials_json`,
с учётом:
- лимита модели,
- запрошенного `max_output_tokens`,
- стоимости медиа.

### MVP-подход (без точного токенайзера)

Ввести лимиты и эвристику:
- `MAX_IMAGES_PER_CALL` (например 12)
- `MAX_ROIS_PER_ITERATION` (например 6)
- `MAX_BLOCKS_IN_MATERIALS` (например 80)
- `MAX_CONTEXT_CHARS` (например 60–120k)
- “стоимость медиа” по resolution:
  - low < medium < high; ограничивать число PNG при high.

### Где применять

- В `AgentService` перед формированием `user_prompt`:
  - обрезать `full_context`,
  - ограничивать рост `materials_json` (не копить бесконечно),
  - ограничивать число PNG/ROI.
- В логировании LLM:
  - фиксировать chars/кол-во blocks/images/rois/итерацию/resolution.

---

## Предлагаемый “боевой” пайплайн обработки запросов (с индексом)

### Первый запрос по документу

1) Проверка индекса в Chat DB: есть/актуален?
2) Если нет:
   - поставить фоновую индексацию (не блокировать ответ),
   - параллельно выполнить текущий ответный пайплайн (как сейчас).
3) Если индекс есть:
   - подобрать кандидатов блоков под вопрос,
   - soft guidance или prefetch (по лимитам),
   - далее стандарт: selected_blocks → evidence PNG/ROI → ответ.

### Повторные запросы

- Всегда используем индекс для candidate selection (быстро),
- затем доказательная часть (MD/HTML + PNG/ROI).

---

## Риски и меры

- **Стоимость/лимиты LLM**: индексатор может “съесть” квоту.
  - Мера: concurrency limits + приоритет интерактива, возможность отключить индексацию.
- **Долгая индексация (500 блоков)**:
  - Мера: batched indexing, прогресс, частичная готовность (top-N сначала).
- **Устаревание индекса**:
  - Мера: `source_version` + `prompt_version` + TTL.
- **Качество индекса**:
  - Мера: строгая schema + re-try, хранение `status/last_error`.

---

## Чек-лист внедрения (по этапам)

### Этап 1 — Кеш рендеров
- Добавить настройки кеша (dir/max_mb/ttl).
- Сделать версионный ключ (etag/last_modified).
- Реализовать eviction (LRU) и/или TTL.

### Этап 2 — BlockIndex (MVP)
- Миграция Chat DB: таблица `document_block_index`.
- Сервис `BlockIndexService` (чтение/поиск кандидатов).
- Сервис `BlockIndexerService` (очередь/батчи/LLM Flash).
- Интеграция в `AgentService`: pre-check индекса, candidate selection.

### Этап 3 — Budgeting
- Лимиты на изображения/ROI/blocks/context.
- Умная обрезка контекста + ограничение роста materials_json.
- Метрики/логирование для наблюдаемости.

---

## Примечания по качеству анализа чертежей

- BlockIndex нужен для “найти правильный лист”, а не для доказательств.
- Доказательства делаются через текущий механизм:
  - `EvidenceService` (overview/quadrants/roi PNG),
  - итерации follow-up в `AgentService`,
  - строгие схемы `AnswerResponse`/`FlashCollectorResponse`.


