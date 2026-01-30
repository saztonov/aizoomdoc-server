# Архитектура хранения файлов документов

## Обзор

Система использует двухуровневую архитектуру хранения:
1. **Cloudflare R2** - объектное хранилище для файлов
2. **Supabase PostgreSQL** - метаданные и связи файлов

## Структура в R2

```
tree_docs/
  └── {document_id}/                    # UUID документа из tree_nodes
      ├── {document_name}_document.md   # Markdown с распознанным текстом
      ├── {document_name}_blocks.json   # Индекс блоков с crop_url
      ├── {document_name}_ocr.html      # HTML версия OCR
      └── crops/                        # Папка с кропами изображений
          ├── {block_id}.pdf            # Кроп блока (PDF формат)
          ├── {block_id}.png            # Или PNG формат
          └── ...
```

### Пример реальных путей

```
tree_docs/d802aac1-7ba0-4a71-93ae-4885d3b89a6a/
├── 133_23-ГК-АР4_изм.7_document.md
├── 133_23-ГК-АР4_изм.7_blocks.json
└── crops/
    ├── 7XHQ-JWRX-9HP.pdf
    ├── 6T9D-7HY7-WQ3.pdf
    └── 7EKF-WFAF-4EX.pdf
```

### Публичный URL

```
https://pub-9530315f35b34246a04e8ad8144e46d5.r2.dev/{r2_key}
```

Пример:
```
https://pub-9530315f35b34246a04e8ad8144e46d5.r2.dev/tree_docs/d802aac1-7ba0-4a71-93ae-4885d3b89a6a/crops/7XHQ-JWRX-9HP.pdf
```

## Структура БД (Projects Supabase)

### Таблица `tree_nodes`

Дерево документов и папок.

| Колонка | Тип | Описание |
|---------|-----|----------|
| id | uuid | PK, UUID документа/папки |
| parent_id | uuid | FK на parent tree_node |
| node_type | text | 'document', 'folder', 'project' |
| name | text | Имя файла/папки |
| code | text | Код/шифр документа |
| client_id | text | ID клиента |

### Таблица `jobs`

Задачи OCR обработки документов.

| Колонка | Тип | Описание |
|---------|-----|----------|
| id | uuid | PK |
| node_id | uuid | FK на tree_nodes.id |
| document_id | text | Хеш PDF файла |
| document_name | text | Имя документа |
| status | text | Статус: pending, processing, completed, failed |
| client_id | text | ID клиента |

**Связь**: `tree_nodes.id` ← `jobs.node_id`

### Таблица `job_files`

Файлы, созданные в результате OCR обработки.

| Колонка | Тип | Описание |
|---------|-----|----------|
| id | uuid | PK |
| job_id | uuid | FK на jobs.id |
| file_type | text | Тип файла (см. ниже) |
| r2_key | text | Путь к файлу в R2 |
| file_name | text | Имя файла |
| mime_type | text | MIME тип |
| file_size | bigint | Размер в байтах |
| metadata | jsonb | Доп. метаданные |

**Связь**: `jobs.id` ← `job_files.job_id`

#### Типы файлов (file_type)

| file_type | Описание | Пример r2_key |
|-----------|----------|---------------|
| `pdf` | Исходный PDF | `tree_docs/{doc_id}/{name}.pdf` |
| `result_md` | Markdown документ | `tree_docs/{doc_id}/{name}_document.md` |
| `blocks_index` | JSON индекс блоков | `tree_docs/{doc_id}/{name}_blocks.json` |
| `ocr_html` | HTML версия OCR | `tree_docs/{doc_id}/{name}_ocr.html` |
| `annotation` | JSON аннотация (старый формат) | `tree_docs/{doc_id}/{name}_annotation.json` |
| `crop` | Кроп изображения | `tree_docs/{doc_id}/crops/{block_id}.pdf` |
| `crops_folder` | Папка с кропами | `tree_docs/{doc_id}/crops/` |
| `result_json` | JSON результат | `tree_docs/{doc_id}/{name}_result.json` |
| `result_zip` | ZIP архив результатов | `tree_docs/{doc_id}/{name}_result.zip` |

### Таблица `node_files` (альтернативный способ)

Файлы, привязанные напрямую к узлу дерева.

| Колонка | Тип | Описание |
|---------|-----|----------|
| id | uuid | PK |
| node_id | uuid | FK на tree_nodes.id |
| file_type | text | Тип файла |
| r2_key | text | Путь к файлу в R2 |
| file_name | text | Имя файла |

**Связь**: `tree_nodes.id` ← `node_files.node_id`

## Формат файла blocks_index.json

```json
{
  "blocks": [
    {
      "id": "7XHQ-JWRX-9HP",
      "page_index": 9,
      "block_type": "image",
      "category_code": null,
      "crop_url": "https://pub-9530315f35b34246a04e8ad8144e46d5.r2.dev/tree_docs/{doc_id}/crops/7XHQ-JWRX-9HP.pdf"
    },
    {
      "id": "LEGW-HLEG-TYM",
      "page_index": 9,
      "block_type": "text",
      "category_code": "specification",
      "crop_url": null
    }
  ]
}
```

### Поля блока

| Поле | Тип | Описание |
|------|-----|----------|
| id | string | Уникальный ID блока (формат: XXXX-XXXX-XXX) |
| page_index | int | Номер страницы (0-based) |
| block_type | string | Тип: "image", "text", "table" |
| category_code | string | Категория содержимого (опционально) |
| crop_url | string | Прямая ссылка на кроп (только для image) |

## Формат Markdown документа (*_document.md)

```markdown
# {Путь в дереве} / {Имя файла}

Сгенерировано: YYYY-MM-DD HH:MM:SS UTC
**Штамп:** Шифр: ... | Стадия: ... | Объект: ...

---

## СТРАНИЦА 1

### BLOCK [TEXT]: 7EW4-WCXE-QPW
Текстовое содержимое блока...

### BLOCK [IMAGE]: 9M7C-JRQM-LUD
[ИЗОБРАЖЕНИЕ] | Тип: Чертеж. Краткое описание: План этажа...
→LEGW-HLEG-TYM

### BLOCK [TABLE]: DEMG-P7YQ-YE6
| Колонка 1 | Колонка 2 |
|-----------|-----------|
| Значение  | Значение  |

## СТРАНИЦА 2
...
```

### Соглашения

- `### BLOCK [TYPE]: ID` - заголовок блока
- `→ID` - ссылка на связанный блок
- Номер страницы в заголовке `## СТРАНИЦА N`

## Цепочка поиска кропа по block_id

```
1. tree_nodes.id (document_id)
       ↓
2. jobs WHERE node_id = document_id
       ↓
3. job_files WHERE job_id IN (job_ids) AND file_type = 'blocks_index'
       ↓
4. Скачать r2_key → JSON
       ↓
5. Найти block.id == image_id → block.crop_url
       ↓
6. Скачать crop_url → PDF/PNG
```

### Fallback (если blocks_index не найден)

```
1. node_files WHERE node_id = document_id AND file_type = 'crop'
       ↓
2. Нормализовать r2_key → извлечь block_id из имени файла
       ↓
3. Найти совпадение с image_id
       ↓
4. Скачать r2_key → PDF/PNG
```

## Код для получения кропа

### Python (agent_service.py)

```python
async def _find_crop_by_image_id(
    self,
    image_id: str,
    document_ids: List[UUID]
) -> Optional[Dict[str, Any]]:
    """
    Найти crop по image_id.

    Приоритет:
    1. blocks_index (job_files) - новый формат с прямыми crop_url
    2. node_files (file_type='crop') - старый формат с r2_key
    """
    # 1. Поиск в blocks_index
    for doc_id in document_ids:
        blocks_index = await self.projects_db.get_blocks_index_for_node(doc_id)
        if blocks_index and blocks_index.get("r2_key"):
            data = await self._download_bytes(blocks_index["r2_key"])
            blocks_data = json.loads(data)
            for block in blocks_data.get("blocks", []):
                if block.get("id") == image_id and block.get("crop_url"):
                    return {"crop_url": block["crop_url"], ...}

    # 2. Fallback на node_files
    crops = await self.projects_db.get_document_crops(doc_id)
    # ... нормализация и поиск
```

### SQL запросы

```sql
-- Получить blocks_index для документа
SELECT jf.*
FROM job_files jf
JOIN jobs j ON j.id = jf.job_id
WHERE j.node_id = '{document_id}'
  AND jf.file_type = 'blocks_index'
LIMIT 1;

-- Получить кропы из node_files (fallback)
SELECT *
FROM node_files
WHERE node_id = '{document_id}'
  AND file_type = 'crop';
```

## Диагностика проблем

### Кроп не найден

1. Проверить наличие job для документа:
```sql
SELECT * FROM jobs WHERE node_id = '{document_id}';
```

2. Проверить наличие blocks_index в job_files:
```sql
SELECT * FROM job_files
WHERE job_id IN (SELECT id FROM jobs WHERE node_id = '{document_id}')
  AND file_type = 'blocks_index';
```

3. Проверить наличие кропов в node_files:
```sql
SELECT * FROM node_files
WHERE node_id = '{document_id}'
  AND file_type = 'crop';
```

4. Скачать и проверить blocks_index.json:
```bash
curl "https://pub-9530315f35b34246a04e8ad8144e46d5.r2.dev/{r2_key}" | jq '.blocks[] | select(.id == "{block_id}")'
```
