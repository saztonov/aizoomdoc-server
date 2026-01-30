"""
API роутер для работы с чатами.
"""

import logging
from uuid import UUID
from typing import List, Optional, AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_current_user_id, get_current_user
from app.db.supabase_client import SupabaseClient
from app.db.supabase_projects_client import SupabaseProjectsClient
from app.db.s3_client import S3Client
from app.services.agent_service import AgentService
from app.services.queue_service import queue_service
from app.models.api import (
    ChatCreate,
    ChatResponse,
    MessageCreate,
    MessageResponse,
    MessageImage,
    ChatHistoryResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    chat_data: ChatCreate,
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends()
):
    """
    Создать новый чат.
    
    Args:
        chat_data: Данные для создания чата
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        Созданный чат
    
    Raises:
        HTTPException: При ошибке создания
    """
    title = chat_data.title or "Новый чат"
    
    chat = await supabase.create_chat(
        user_id=user_id,
        title=title,
        description=chat_data.description
    )
    
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create chat"
        )
    
    return ChatResponse(
        id=chat.id,
        title=chat.title,
        description=chat.description,
        user_id=chat.user_id,
        created_at=chat.created_at,
        updated_at=chat.updated_at
    )


@router.get("", response_model=List[ChatResponse])
async def get_user_chats(
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends()
):
    """
    Получить список чатов пользователя.
    
    Args:
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        Список чатов
    """
    chats = await supabase.get_user_chats(user_id, limit=50)
    
    return [
        ChatResponse(
            id=chat.id,
            title=chat.title,
            description=chat.description,
            user_id=chat.user_id,
            created_at=chat.created_at,
            updated_at=chat.updated_at
        )
        for chat in chats
    ]


@router.get("/{chat_id}", response_model=ChatHistoryResponse)
async def get_chat_history(
    chat_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends()
):
    """
    Получить историю чата.
    
    Админы могут просматривать любые чаты.
    Обычные пользователи - только свои.
    
    Args:
        chat_id: UUID чата
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        История чата с сообщениями
    
    Raises:
        HTTPException: Если чат не найден или нет доступа
    """
    chat = await supabase.get_chat(chat_id)
    
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    # Проверяем права доступа: админы видят все чаты, пользователи - только свои
    user = await supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not user.is_admin and chat.user_id != user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Получаем сообщения
    messages = await supabase.get_chat_messages(chat_id)
    
    # Получаем изображения для каждого сообщения
    from app.config import settings
    
    message_responses = []
    for msg in messages:
        images_data = await supabase.get_message_images(msg.id)
        
        images = []
        for img in images_data:
            # Генерируем URL для изображения
            storage_path = img.get("storage_path")
            external_url = img.get("external_url")
            source_type = img.get("source_type")

            if external_url:
                url = external_url
            elif storage_path:
                # Для projects_crop используем S3_PROJECTS_DEV_URL
                if source_type == "projects_crop" and settings.s3_projects_dev_url:
                    base_url = settings.s3_projects_dev_url.rstrip('/')
                    url = f"{base_url}/{storage_path}"
                elif settings.use_s3_dev_url and settings.s3_dev_url:
                    # Для остальных файлов - S3_DEV_URL
                    base_url = settings.s3_dev_url.rstrip('/')
                    url = f"{base_url}/{storage_path}"
                else:
                    url = None
            else:
                url = None
            
            images.append(MessageImage(
                id=img.get("id"),
                file_id=img.get("file_id"),
                image_type=img.get("image_type"),
                description=img.get("description"),
                width=img.get("width"),
                height=img.get("height"),
                url=url
            ))
        
        message_responses.append(MessageResponse(
            id=msg.id,
            chat_id=msg.chat_id,
            role=msg.role,
            content=msg.content,
            message_type=msg.message_type,
            created_at=msg.created_at,
            images=images
        ))
    
    return ChatHistoryResponse(
        chat=ChatResponse(
            id=chat.id,
            title=chat.title,
            description=chat.description,
            user_id=chat.user_id,
            created_at=chat.created_at,
            updated_at=chat.updated_at
        ),
        messages=message_responses
    )


@router.delete("/{chat_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_chat(
    chat_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends()
):
    """
    Удалить чат (асинхронно).
    
    Запускает каскадное удаление в фоновом режиме:
    - Файлы из R2
    - Локальные логи сервера
    - Записи из БД (messages, chat_images, chats)
    
    Админы могут удалять любые чаты.
    Обычные пользователи - только свои.
    
    Args:
        chat_id: UUID чата
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        202 Accepted - удаление запланировано
    
    Raises:
        HTTPException: Если чат не найден или нет доступа
    """
    from app.services.deletion_service import deletion_service
    
    chat = await supabase.get_chat(chat_id)
    
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    # Проверяем права доступа: админы могут удалять любые чаты
    user = await supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not user.is_admin and chat.user_id != user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Запланировать удаление (неблокирующее)
    deletion_service.schedule_deletion(chat_id)
    
    logger.info(f"Chat {chat_id} scheduled for deletion by user {user_id}")
    
    return {"message": "Deletion scheduled", "chat_id": str(chat_id)}


@router.post("/{chat_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    chat_id: UUID,
    message_data: MessageCreate,
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends()
):
    """
    Отправить сообщение в чат.
    
    Note: Это создает только пользовательское сообщение.
          Обработка и ответ LLM происходят через WebSocket.
    
    Админы могут писать в любые чаты.
    Обычные пользователи - только в свои.
    
    Args:
        chat_id: UUID чата
        message_data: Данные сообщения
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        Созданное сообщение
    
    Raises:
        HTTPException: Если чат не найден или нет доступа
    """
    chat = await supabase.get_chat(chat_id)
    
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    # Проверяем права доступа: админы могут писать в любые чаты
    user = await supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not user.is_admin and chat.user_id != user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Создаем сообщение
    message = await supabase.add_message(
        chat_id=chat_id,
        role="user",
        content=message_data.content
    )

    # TODO: сохранить attached_file_ids / attached_document_ids при необходимости
    
    if not message:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create message"
        )
    
    return MessageResponse(
        id=message.id,
        chat_id=message.chat_id,
        role=message.role,
        content=message.content,
        message_type=message.message_type,
        created_at=message.created_at
    )


@router.get("/{chat_id}/stream")
async def chat_stream_sse(
    chat_id: UUID,
    client_id: Optional[str] = Query(default=None, description="ID клиента (projects)"),
    document_ids: Optional[List[UUID]] = Query(default=None, description="ID документов для контекста"),
    compare_document_ids_a: Optional[List[UUID]] = Query(default=None, description="ID документов для сравнения (A)"),
    compare_document_ids_b: Optional[List[UUID]] = Query(default=None, description="ID документов для сравнения (B)"),
    google_files: Optional[str] = Query(default=None, description="JSON с файлами из Google File API"),
    tree_files: Optional[str] = Query(default=None, description="JSON с файлами MD/HTML из дерева [{r2_key, file_type}]"),
    current_user=Depends(get_current_user),
    supabase: SupabaseClient = Depends(),
    projects_db: SupabaseProjectsClient = Depends(),
):
    """
    SSE стрим обработки сообщения с очередью запросов.
    
    Ожидает, что сообщение пользователя уже сохранено через POST /chats/{chat_id}/messages.
    
    События очереди:
    - queue_position: позиция в очереди {position, estimated_wait_seconds}
    - processing_started: запрос начал обрабатываться
    
    Далее стандартные события:
    - phase_started, phase_progress, llm_token, llm_final, tool_call, error, completed
    """
    import json as json_module
    
    s3_client = S3Client()
    agent = AgentService(current_user, supabase, projects_db, s3_client)

    # Получаем последнее сообщение пользователя
    last_user_message = await supabase.get_last_message(chat_id, role="user")
    if not last_user_message:
        raise HTTPException(status_code=404, detail="User message not found")

    # Парсим google_files из JSON
    parsed_google_files = None
    if google_files:
        try:
            parsed_google_files = json_module.loads(google_files)
            logger.info(f"Parsed google_files: {parsed_google_files}")
        except Exception as e:
            logger.error(f"Failed to parse google_files: {e}")

    # Парсим tree_files из JSON (файлы MD/HTML из дерева)
    parsed_tree_files = None
    if tree_files:
        try:
            parsed_tree_files = json_module.loads(tree_files)
            logger.info(f"Parsed tree_files: {parsed_tree_files}")
        except Exception as e:
            logger.error(f"Failed to parse tree_files: {e}")

    # Функция-генератор для обработки (будет вызвана из очереди)
    async def process_request():
        async for event in agent.process_message(
            chat_id=chat_id,
            user_message=last_user_message.content,
            client_id=client_id,
            document_ids=document_ids,
            compare_document_ids_a=compare_document_ids_a,
            compare_document_ids_b=compare_document_ids_b,
            google_file_uris=parsed_google_files,
            tree_files=parsed_tree_files,
            save_user_message=False
        ):
            # Конвертируем StreamEvent в dict для очереди
            yield {
                "event": event.event,
                "data": event.data,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None
            }

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Обрабатываем через очередь
            async for event in queue_service.execute_with_queue(chat_id, process_request):
                event_name = event.get("event", "unknown")
                payload = json_module.dumps(event.get("data", {}), ensure_ascii=False)
                yield f"event: {event_name}\n"
                yield f"data: {payload}\n\n"
        except RuntimeError as e:
            # Очередь переполнена или другая ошибка
            error_payload = json_module.dumps({"message": str(e)}, ensure_ascii=False)
            yield f"event: error\n"
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.websocket("/{chat_id}/stream")
async def chat_stream(
    websocket: WebSocket,
    chat_id: UUID,
    # TODO: Добавить аутентификацию через query параметр token
):
    """
    WebSocket эндпоинт для стриминга обработки сообщения.
    
    Клиент подключается к этому эндпоинту после отправки сообщения
    через POST /chats/{chat_id}/messages.
    
    События стриминга:
    - phase_started: Начало фазы (search, processing, llm, zoom)
    - phase_progress: Прогресс фазы
    - llm_token: Токен от LLM
    - llm_final: Финальный ответ LLM
    - tool_call: Вызов инструмента (request_images, zoom)
    - error: Ошибка
    - completed: Обработка завершена
    
    Args:
        websocket: WebSocket соединение
        chat_id: UUID чата
    """
    await websocket.accept()
    
    try:
        # TODO: Реализовать обработку сообщения и стриминг
        # Это будет реализовано в services/agent_service.py
        await websocket.send_json({
            "event": "error",
            "data": {"message": "WebSocket streaming not implemented yet"},
            "timestamp": "2026-01-13T00:00:00Z"
        })
        
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for chat {chat_id}")
    except Exception as e:
        logger.error(f"Error in WebSocket: {e}")
        await websocket.close(code=1011, reason=str(e))


