"""
API роутер для аутентификации.
"""

import logging
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.core.auth import create_access_token
from app.db.supabase_client import SupabaseClient
from app.models.api import TokenExchangeRequest, TokenExchangeResponse, UserInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/exchange", response_model=TokenExchangeResponse)
async def exchange_token(
    request: TokenExchangeRequest,
    supabase: SupabaseClient = Depends()
):
    """
    Обменять статичный токен на JWT access token.
    
    Args:
        request: Запрос с статичным токеном
        supabase: Клиент Supabase
    
    Returns:
        JWT токены и информация о пользователе
    
    Raises:
        HTTPException: При невалидном токене
    """
    # Найти пользователя по статичному токену
    user = await supabase.get_user_by_static_token(request.static_token)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid static token"
        )
    
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is not active"
        )
    
    # Обновить время последнего входа
    await supabase.update_user_last_seen(user.id)
    
    # Создать access token
    access_token = create_access_token(user.id)
    
    # Вернуть токен и информацию о пользователе
    return TokenExchangeResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserInfo(
            id=user.id,
            username=user.username,
            status=user.status,
            created_at=user.created_at
        )
    )


@router.post("/logout")
async def logout():
    """
    Выход из системы.
    
    Note: В MVP без refresh токенов, просто информационный эндпоинт.
          Клиент должен удалить access token локально.
    
    Returns:
        Успешный ответ
    """
    return {"message": "Successfully logged out"}


