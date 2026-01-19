# Интеграция с Python клиентом

Этот документ описывает, как интегрировать `aizoomdoc-client-py` с сервером.

## Базовая интеграция

### 1. Установка клиента

```bash
pip install aizoomdoc-client
```

### 2. Аутентификация

```python
from aizoomdoc_client import AIZoomDocClient

# Создание клиента
client = AIZoomDocClient(
    server_url="http://localhost:8000",
    static_token="your-static-token"
)

# Обмен токена (автоматически при первом запросе)
client.authenticate()

# Токены сохраняются автоматически и обновляются при истечении
```

### 3. Работа с чатами

```python
# Создание чата
chat = client.create_chat(
    title="Анализ системы В2",
    description="Вопросы по вентиляции"
)

# Отправка сообщения со стримингом
for event in client.send_message(
    chat_id=chat.id,
    message="Какое оборудование установлено в системе В2?"
):
    if event.event == "llm_token":
        print(event.data["token"], end="", flush=True)
    elif event.event == "phase_started":
        print(f"\n[{event.data['phase']}] {event.data['description']}")
    elif event.event == "completed":
        print("\n✓ Готово!")

# Получение истории
history = client.get_chat_history(chat.id)
for message in history.messages:
    print(f"{message.role}: {message.content}")
```

### 4. Управление настройками

```python
# Получение текущих настроек
user_info = client.get_me()
print(f"Режим: {user_info.settings.model_profile}")
print(f"Роль: {user_info.settings.selected_role_prompt_id}")

# Смена режима модели
client.update_settings(model_profile="complex")

# Смена роли
roles = client.get_available_roles()
engineer_role = next(r for r in roles if r.name == "Инженер")
client.update_settings(selected_role_prompt_id=engineer_role.id)
```

### 5. Загрузка файлов

```python
# Загрузка PDF
file_info = client.upload_file("path/to/document.pdf")
print(f"Файл загружен: {file_info.id}")

# Прикрепление к чату
client.send_message(
    chat_id=chat.id,
    message="Проанализируй этот документ",
    attached_file_ids=[file_info.id]
)
```

## CLI интеграция

### Команды клиента

```bash
# Логин
aizoomdoc login --token your-static-token

# Создание чата
aizoomdoc chat new "Анализ ВРУ"

# Отправка сообщения
aizoomdoc chat send "Где находится ВРУ-1?"

# История чата
aizoomdoc chat history

# Настройки
aizoomdoc settings set-model complex
aizoomdoc settings set-role "Инженер"

# Загрузка файла
aizoomdoc file upload document.pdf

# Просмотр проектов
aizoomdoc projects tree --client-id mycompany
```

## WebSocket интеграция

### Прямое подключение (JavaScript)

```javascript
const ws = new WebSocket(
  `ws://localhost:8000/chats/${chatId}/stream?token=${accessToken}`
);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  switch (data.event) {
    case 'phase_started':
      console.log(`[${data.data.phase}] ${data.data.description}`);
      break;
    
    case 'llm_token':
      process.stdout.write(data.data.token);
      break;
    
    case 'llm_final':
      console.log('\n✓ Ответ получен');
      break;
    
    case 'error':
      console.error('Error:', data.data.message);
      break;
    
    case 'completed':
      console.log('✓ Обработка завершена');
      ws.close();
      break;
  }
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};
```

## API Endpoints для клиента

### Auth

```
POST /auth/exchange
POST /auth/logout
```

### User

```
GET /me
PATCH /me/settings
```

### Chats

```
POST /chats
GET /chats
GET /chats/{chat_id}
POST /chats/{chat_id}/messages
WS /chats/{chat_id}/stream
```

### Files

```
POST /files/upload
GET /files/{file_id}
```

### Projects

```
GET /projects/tree
GET /projects/documents/{doc_id}/results
GET /projects/search
```

### Prompts

```
GET /prompts/roles
```

## Обработка ошибок

### Автоматический refresh токенов

```python
# Клиент автоматически обновляет access token при истечении
# Если refresh token истек, нужен повторный login

try:
    result = client.send_message(chat_id, message)
except TokenExpiredError:
    # Refresh token истек
    client.authenticate()  # Повторный exchange
    result = client.send_message(chat_id, message)
```

### Обработка ошибок API

```python
from aizoomdoc_client.exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    ServerError
)

try:
    chat = client.create_chat(title="Test")
except AuthenticationError:
    print("Ошибка аутентификации. Проверьте токен.")
except NotFoundError:
    print("Ресурс не найден.")
except ServerError as e:
    print(f"Ошибка сервера: {e.message}")
except APIError as e:
    print(f"Ошибка API: {e}")
```

## Примеры использования

### Пример 1: Простой вопрос

```python
client = AIZoomDocClient(
    server_url="http://localhost:8000",
    static_token="your-token"
)

chat = client.create_chat(title="Быстрый вопрос")

# В simple режиме (по умолчанию)
response = ""
for event in client.send_message(chat.id, "Какая высота этажа?"):
    if event.event == "llm_token":
        response += event.data["token"]

print(f"Ответ: {response}")
```

### Пример 2: Сложный анализ

```python
# Переключаемся в complex режим
client.update_settings(model_profile="complex")

# Выбираем роль
roles = client.get_available_roles()
engineer = next(r for r in roles if r.name == "Инженер")
client.update_settings(selected_role_prompt_id=engineer.id)

# Создаем чат для анализа
chat = client.create_chat(
    title="Детальный анализ ВРУ",
    description="Проверка соответствия нормам"
)

# Отправляем сложный запрос
message = """
Проанализируй размещение ВРУ-1 на плане:
1. Соответствие нормативным расстояниям
2. Доступность для обслуживания
3. Пути эвакуации
"""

for event in client.send_message(chat.id, message):
    if event.event == "phase_started":
        print(f"→ {event.data['description']}")
    elif event.event == "llm_token":
        print(event.data["token"], end="", flush=True)

print("\n✓ Анализ завершен")
```

### Пример 3: Работа с файлами

```python
# Загрузка нескольких PDF
files = []
for pdf_path in ["plan1.pdf", "plan2.pdf", "plan3.pdf"]:
    file_info = client.upload_file(pdf_path)
    files.append(file_info.id)
    print(f"✓ {pdf_path} загружен")

# Создание чата с вложениями
chat = client.create_chat(title="Сравнение планов")

# Отправка с вложениями
for event in client.send_message(
    chat_id=chat.id,
    message="Сравни системы вентиляции на этих планах",
    attached_file_ids=files
):
    if event.event == "llm_token":
        print(event.data["token"], end="", flush=True)
```

## Troubleshooting

### Проблема: "Invalid token"

```python
# Решение: Повторная аутентификация
client.clear_tokens()
client.authenticate()
```

### Проблема: "Connection timeout"

```python
# Решение: Увеличить timeout
client = AIZoomDocClient(
    server_url="http://localhost:8000",
    static_token="token",
    timeout=60  # секунд
)
```

### Проблема: "Gemini API key not configured"

```python
# Решение: У пользователя нет API ключа и нет дефолтного
# Свяжитесь с администратором для настройки ключа
```


