"""
Модели для внутренней логики (БД, сервисы).
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field


# ===== USER MODELS =====

class User(BaseModel):
    """Модель пользователя из БД."""
    id: UUID
    username: str
    static_token: str  # В MVP храним в открытом виде
    status: str = Field(default="active")
    created_at: datetime
    last_seen_at: Optional[datetime] = None


class UserWithSettings(BaseModel):
    """Пользователь с настройками."""
    user: User
    settings: "Settings"
    gemini_api_key: Optional[str] = None


# ===== SETTINGS MODELS =====

class Settings(BaseModel):
    """Настройки пользователя из БД."""
    user_id: UUID
    model_profile: Literal["simple", "complex"] = Field(default="simple")
    selected_role_prompt_id: Optional[int] = None  # bigint в БД
    page_settings: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ===== PROMPT MODELS =====

class SystemPrompt(BaseModel):
    """Системный промпт."""
    id: UUID
    name: str
    content: str
    description: Optional[str]
    is_active: bool = True
    version: int = 1
    created_at: datetime
    updated_at: datetime


class UserPrompt(BaseModel):
    """Пользовательский промпт (роль)."""
    id: int
    user_id: str
    name: str
    content: str
    created_at: datetime
    updated_at: datetime


# ===== CHAT MODELS =====

class Chat(BaseModel):
    """Модель чата из БД."""
    id: UUID
    title: str
    description: Optional[str]
    user_id: str
    is_archived: bool = False
    document_file_id: Optional[UUID] = None
    document_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    """Модель сообщения из БД."""
    id: UUID
    chat_id: UUID
    role: Literal["user", "assistant", "system"]
    content: str
    message_type: str = Field(default="text")
    created_at: datetime


class ChatImage(BaseModel):
    """Изображение, прикрепленное к сообщению."""
    id: UUID
    chat_id: UUID
    message_id: UUID
    file_id: Optional[UUID]
    image_type: Optional[str]
    description: Optional[str]
    width: Optional[int]
    height: Optional[int]
    uploaded_at: datetime


# ===== STORAGE MODELS =====

class StorageFile(BaseModel):
    """Файл в хранилище."""
    id: UUID
    user_id: Optional[str]
    source_type: str
    storage_path: Optional[str]
    external_url: Optional[str]
    filename: Optional[str]
    mime_type: Optional[str]
    size_bytes: Optional[int]
    created_at: datetime


# ===== LLM MODELS =====

class ViewportCrop(BaseModel):
    """Кроп viewport для изображения."""
    image_id: str
    image_path: str
    description: str
    block_id: Optional[str] = None
    page: Optional[int] = None
    coords_norm: Optional[List[float]] = None
    coords_px: Optional[List[int]] = None


class ZoomRequest(BaseModel):
    """Запрос zoom."""
    image_id: str
    coords_norm: Optional[List[float]] = None
    coords_px: Optional[List[int]] = None
    reason: str
    is_zoom_request: bool = True


class ImageRequest(BaseModel):
    """Запрос дополнительных изображений."""
    image_ids: List[str]
    reason: str


class DocumentRequest(BaseModel):
    """Запрос дополнительной документации."""
    documents: List[str]
    reason: str


class LLMResponse(BaseModel):
    """Ответ от LLM."""
    answer_markdown: str
    confidence_score: Optional[float] = None
    used_sources: Optional[List[str]] = None
    zoom_requests: Optional[List[ZoomRequest]] = None
    image_requests: Optional[ImageRequest] = None
    document_requests: Optional[DocumentRequest] = None
    raw_response: Optional[str] = None


# ===== SEARCH MODELS =====

class TextBlock(BaseModel):
    """Текстовый блок из документа."""
    text: str
    block_id: Optional[str] = None
    page: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """Результат поиска."""
    text_blocks: List[TextBlock]
    images: List[ViewportCrop] = Field(default_factory=list)
    query: str
    total_blocks_found: int

