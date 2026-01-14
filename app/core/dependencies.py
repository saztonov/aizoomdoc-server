"""
Dependency для получения текущего пользователя из JWT.
"""

from typing import Optional
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.auth import get_user_id_from_token
from app.db.supabase_client import SupabaseClient
from app.models.internal import UserWithSettings


security = HTTPBearer()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> UUID:
    """
    Получить ID текущего пользователя из JWT токена.
    
    Args:
        credentials: HTTP Authorization credentials
    
    Returns:
        UUID пользователя
    
    Raises:
        HTTPException: При невалидном токене
    """
    token = credentials.credentials
    return get_user_id_from_token(token)


async def get_current_user(
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends()
) -> UserWithSettings:
    """
    Получить полную информацию о текущем пользователе.
    
    Args:
        user_id: ID пользователя из токена
        supabase: Клиент Supabase
    
    Returns:
        Пользователь с настройками
    
    Raises:
        HTTPException: Если пользователь не найден
    """
    user_with_settings = await supabase.get_user_with_settings(user_id)
    
    if not user_with_settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user_with_settings.user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is not active"
        )
    
    return user_with_settings


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[UUID]:
    """
    Получить ID пользователя из токена (если есть).
    Для публичных эндпоинтов с опциональной авторизацией.
    
    Args:
        credentials: HTTP Authorization credentials (опционально)
    
    Returns:
        UUID пользователя или None
    """
    if not credentials:
        return None
    
    try:
        return get_user_id_from_token(credentials.credentials)
    except HTTPException:
        return None

