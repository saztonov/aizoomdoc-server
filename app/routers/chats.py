"""
API роутер для работы с чатами.
"""

import logging
from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect

from app.core.dependencies import get_current_user_id
from app.db.supabase_client import SupabaseClient
from app.models.api import (
    ChatCreate,
    ChatResponse,
    MessageCreate,
    MessageResponse,
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
    
    Args:
        chat_id: UUID чата
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        История чата с сообщениями
    
    Raises:
        HTTPException: Если чат не найден или не принадлежит пользователю
    """
    chat = await supabase.get_chat(chat_id)
    
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    # Проверяем принадлежность чата пользователю
    user = await supabase.get_user_by_id(user_id)
    if not user or chat.user_id != user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    # Получаем сообщения
    messages = await supabase.get_chat_messages(chat_id)
    
    return ChatHistoryResponse(
        chat=ChatResponse(
            id=chat.id,
            title=chat.title,
            description=chat.description,
            user_id=chat.user_id,
            created_at=chat.created_at,
            updated_at=chat.updated_at
        ),
        messages=[
            MessageResponse(
                id=msg.id,
                chat_id=msg.chat_id,
                role=msg.role,
                content=msg.content,
                message_type=msg.message_type,
                created_at=msg.created_at
            )
            for msg in messages
        ]
    )


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
    
    Args:
        chat_id: UUID чата
        message_data: Данные сообщения
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        Созданное сообщение
    
    Raises:
        HTTPException: Если чат не найден или не принадлежит пользователю
    """
    chat = await supabase.get_chat(chat_id)
    
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    
    # Проверяем принадлежность чата пользователю
    user = await supabase.get_user_by_id(user_id)
    if not user or chat.user_id != user.username:
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

