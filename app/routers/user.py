"""
API роутер для работы с настройками и профилем пользователя.
"""

import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.dependencies import get_current_user, get_current_user_id
from app.db.supabase_client import SupabaseClient
from app.models.api import UserMeResponse, UserSettings, UserSettingsUpdate, PromptUserRole
from app.models.internal import UserWithSettings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["user"])


@router.get("", response_model=UserMeResponse)
async def get_current_user_info(
    current_user: UserWithSettings = Depends(get_current_user)
):
    """
    Получить информацию о текущем пользователе и его настройках.
    
    Args:
        current_user: Текущий пользователь из JWT
    
    Returns:
        Информация о пользователе и настройках
    """
    return UserMeResponse(
        user={
            "id": current_user.user.id,
            "username": current_user.user.username,
            "status": current_user.user.status,
            "created_at": current_user.user.created_at
        },
        settings=UserSettings(
            model_profile=current_user.settings.model_profile,
            selected_role_prompt_id=current_user.settings.selected_role_prompt_id
        ),
        gemini_api_key_configured=current_user.gemini_api_key is not None
    )


@router.patch("/settings", response_model=UserSettings)
async def update_user_settings(
    settings_update: UserSettingsUpdate,
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends()
):
    """
    Обновить настройки пользователя.
    
    Args:
        settings_update: Данные для обновления
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
    
    Returns:
        Обновленные настройки
    
    Raises:
        HTTPException: При ошибке обновления
    """
    # Проверяем существование роли если она указана
    if settings_update.selected_role_prompt_id is not None:
        role = await supabase.get_user_prompt_by_id(settings_update.selected_role_prompt_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role prompt not found"
            )
    
    # Обновляем настройки
    updated_settings = await supabase.update_user_settings(
        user_id=user_id,
        model_profile=settings_update.model_profile,
        selected_role_prompt_id=settings_update.selected_role_prompt_id
    )
    
    if not updated_settings:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update settings"
        )
    
    return UserSettings(
        model_profile=updated_settings.model_profile,
        selected_role_prompt_id=updated_settings.selected_role_prompt_id
    )


