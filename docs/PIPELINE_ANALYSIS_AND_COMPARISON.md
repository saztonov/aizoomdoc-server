# Пайплайн анализа рабочей документации (AIZoomDoc v2) и сравнение режимов

**Дата**: 2026‑01‑21  
**Репозиторий**: `aizoomdoc-server`  
**Фактическая реализация**: см. `app/services/agent_service.py`, `app/services/llm_service.py`, `app/services/evidence_service.py`

---

## 1) Архитектура на уровне компонентов

### Основные сервисы
- **`AgentService`** (`app/services/agent_service.py`): оркестратор. Управляет фазами обработки, вызывает LLM, собирает материалы, организует followup-итерации.
- **`LLMService`** (`app/services/llm_service.py`): единая обёртка над Gemini (strict JSON schema). Важные вызовы:
  - `run_flash_collector()` — сбор материалов (Flash).
  - `run_answer()` — финальный ответ (Flash или Pro, в strict JSON AnswerResponse).
  - `run_analysis_intent()` — классификация намерения (новое).
- **`HtmlOcrService`** (`app/services/html_ocr_service.py`): извлекает `block_id -> crop_url` из HTML OCR, чтобы можно было загрузить PDF-кроп и отрендерить PNG.
- **`EvidenceService`** (`app/services/evidence_service.py`): рендер PDF-кропа в PNG:
  - overview (preview),
  - quadrants (если лист большой),
  - ROI (zoom) по `bbox_norm` + dpi.
- **`DocumentExtractService`** (`app/services/document_extract_service.py`): универсальное извлечение фактов и таблиц из блоков (новое, без словарей).
- **`LLMDialogLogger`** (`app/services/llm_logger.py`): подробные логи запросов/ответов LLM (включая новые секции `ANALYSIS_INTENT`, `DOCUMENT_FACTS`, `QUALITY_GATE`).

### Хранилища и внешние зависимости
- **Supabase**: пользователи/настройки/чаты/сообщения/файлы.
- **S3/R2**: хранение загруженных документов и отрендеренных PNG (chat_images, llm_uploads).
- **Google File API (Gemini files)**: передача HTML и PNG в контекст LLM (через `Part.from_uri`).

---

## 2) Общие сущности данных (важно для понимания пайплайна)

Схемы в `app/models/llm_schemas.py`:
- **`FlashCollectorResponse`**: `selected_blocks`, `requested_images`, `requested_rois`, `materials_summary`.
- **`MaterialsJSON`**: `blocks`, `images`, `source_documents`, **`extracted_facts` (новое)**.
- **`AnswerResponse`**: `answer_markdown`, `citations`, `issues`, `recommendations`, `needs_more_evidence`, `followup_images`, `followup_rois`.
- **`AnalysisIntent` (новое)**: `intent_type`, `requires_visual_detail`, `focus_areas`, `confidence`, `rationale`.
- **`DocumentFacts` (новое)**: `facts[]` и `tables[]` для универсальной “фактовой базы” без словаря.

Ключевая идея последних изменений: **разделить “извлечение” и “интерпретацию”**.
- Flash: собирает источники (блоки/изображения/ROI), без выводов.
- Extractor: структурирует факты/таблицы из текстовых блоков.
- Pro: отвечает и даёт рекомендации, но обязан ссылаться на доказательства.

---

## 3) Пайплайн FLASH+PRO (complex) — фактическая последовательность

Реализация: `AgentService._process_complex_mode()`.

### Фаза 0: Intent Router (новое)
1) `analysis_intent = _classify_intent(...)` на основе вопроса + короткого контекста.
2) `intent_note = _format_intent_note(analysis_intent)` добавляется в промпты Flash/Pro (как отдельный блок `ANALYSIS_INTENT`).

**Промпт**: `data/promts/analysis_router_prompt.txt`  
**Вызов**: `LLMService.run_analysis_intent()`

### Фаза 1: Flash collector
1) Собираются payloads документов (MD/HTML), строится `block_map`.
2) Flash получает: `full_text` + `html_note` (если есть HTML) + `intent_note` + `USER QUESTION`.
3) Flash возвращает `FlashCollectorResponse` (строго по схеме).
4) Применяется `_apply_coverage_check()`:
   - добавляет linked-блоки,
   - добавляет дополнительные блоки по скорингу,
   - автоматически просит изображения для IMAGE-блоков, попавших в выборку.

**Промпт**: `data/promts/flash_extractor_prompt.txt` (обновлён: подчёркнуто, что подсчёт вторичен и нельзя делать выводы).

### Фаза 1.5: Document Extract (новое)
Из `combined_blocks` извлекаются универсальные факты/таблицы:
- `DocumentExtractService.extract_facts(...)`
- результаты логируются как `DOCUMENT_FACTS`

**Промпт**: `data/promts/document_extract_prompt.txt`

### Фаза 2: Materials builder (PNG-only)
1) На основе `requested_images/requested_rois` строится `materials_json`.
2) `EvidenceService` создаёт:
   - overview,
   - quadrants (если нужно),
   - ROI по запросу.
3) PNG загружаются:
   - в Google File API (для LLM),
   - в R2 (для клиента), и регистрируются в БД.
4) `materials_json.extracted_facts = extracted_facts` (новое).

### Фаза 3: Pro answer + followup loop
1) Pro получает `ANALYSIS_INTENT` + `MATERIALS_JSON` + `USER QUESTION`.
2) Pro возвращает `AnswerResponse`.
3) Если есть `followup_images`/`followup_rois` — запускается итерация:
   - `tool_execution` (рендер и upload),
   - повторный `pro_answer_N`.

### Quality Gate: политика обязательных ROI/zoom (новое)
В complex‑режиме добавлен жёсткий триггер:
- если `analysis_intent.requires_visual_detail == true`,
- и Pro **не** запросил followup,
- и в citations **нет** `kind="roi"`,
→ считается, что доказательств недостаточно, и система принудительно инициирует followup:
1) либо `followup_images` (если ещё нет изображений),
2) либо отдельный запрос `roi_request_prompt` для генерации `followup_rois`.

**Промпт**: `data/promts/roi_request_prompt.txt`

---

## 4) Пайплайн FLASH (simple, flash-only) — фактическая последовательность

Реализация: `AgentService._process_simple_mode()`.

Что совпадает с complex:
- Есть **Intent Router** (`_classify_intent`) и лог `ANALYSIS_INTENT`.
- Есть **DocumentExtractService**: `extracted_facts` добавляются в `materials_json`.
- Есть followup-цикл на `followup_images`/`followup_rois`.
- Применяется **Quality Gate**: при `requires_visual_detail` и отсутствии ROI → принудительный followup.
- Используется тот же `MaterialsJSON` и тот же рендер изображений через `_build_materials`.

Что отличается (важно):
- **Нет Flash‑collector** как отдельного этапа сборки материалов. В simple режиме LLM отвечает напрямую (flash_answer / llm_system_prompt), без “двухэтапного” сбора.
- **Контекст шире/сырее**: материалы собираются из всех блоков документов (без Flash‑фильтрации), что может увеличить шум.
- В followup-итерации `_build_materials` вызывается с `selected_blocks=[]`, то есть followup добавляет только изображения/ROI, не расширяя текстовые блоки.

Вывод: **FLASH режим теперь сопоставим по строгости доказательств**, но всё ещё проще по структуре (без Flash‑collector).

---

## 5) Сравнение документов (compare-mode) — фактическая последовательность

Реализация: `AgentService._process_compare_mode()`.

Что делает:
- Для каждого документа из A и B:
  - запускает Flash‑collector (как в complex),
  - подписывает блоки префиксом `[DOC_A: ...]` / `[DOC_B: ...]`,
  - объединяет блоки/изображения/ROI.
- Собирает `materials_json` и вызывает Pro с вопросом вида `Compare DOC_A vs DOC_B. ...`.
- Имеет followup-цикл по `followup_images/followup_rois`.

Что отличается от complex:
- Здесь **есть** intent‑router, извлечение фактов и quality‑gate (как в complex), но вопрос сравнения формируется с префиксом `Compare DOC_A vs DOC_B.`.
- Блоки обоих документов помечаются `[DOC_A]` и `[DOC_B]`, что влияет на extract и финальные выводы.

Вывод: **compare‑mode теперь выровнен по качеству и строгой проверке доказательств**, с сохранением специфики “меток” источника.

---

## 6) Результаты сравнения (сводная таблица)

| Возможность / слой | FLASH (simple) | FLASH+PRO (complex) | Compare (A vs B) |
|---|---:|---:|---:|
| Intent Router (`analysis_router_prompt`) | да | да | да |
| Flash‑collector (сбор материалов до ответа) | нет | да | да |
| DocumentExtractService (`document_extract_prompt`) | да | да | да |
| `materials_json.extracted_facts` | да | да | да |
| Followup loop (images/rois) | да | да | да |
| Quality Gate: принудительный ROI при `requires_visual_detail` | да | да | да |
| Политика “не гадать без ROI” | промпт + кодовая принудиловка | промпт + кодовая принудиловка | промпт + кодовая принудиловка |

---

## 7) Регрессионные сценарии

Список ручных сценариев для проверки поведения добавлен в `docs/REGRESSION_SCENARIOS.md`.

---

## 8) Рекомендации по выравниванию режимов (если нужно)

Если потребуется усилить сравнение:
1) Делать **раздельное извлечение** фактов для DOC_A и DOC_B (вместо общего списка), чтобы риски/решения не смешивались.
2) Ввести отдельную секцию в `answer_markdown` для различий и единый блок “что совпадает”.


