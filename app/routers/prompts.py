"""
API роутер для работы с промптами.
"""

import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from app.db.supabase_client import SupabaseClient
from app.models.api import PromptUserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("/roles", response_model=List[PromptUserRole])
async def get_available_roles(
    supabase: SupabaseClient = Depends()
):
    """
    Получить список доступных ролей (пользовательских промптов).
    
    Args:
        supabase: Клиент Supabase
    
    Returns:
        Список доступных ролей
    """
    roles = await supabase.get_user_prompts(active_only=True)
    
    return [
        PromptUserRole(
            id=role.id,
            name=role.name,
            content=role.content,
            description=None,  # У user_prompts нет поля description
            is_active=True,
            version=1,
            created_at=role.created_at,
            updated_at=role.updated_at
        )
        for role in roles
    ]

