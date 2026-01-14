"""
API роутер для работы с файлами.
"""

import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File

from app.config import settings
from app.core.dependencies import get_current_user_id
from app.db.supabase_client import SupabaseClient
from app.db.s3_client import S3Client
from app.models.api import FileUploadResponse, FileInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends(),
    s3: S3Client = Depends()
):
    """
    Загрузить файл.
    
    Args:
        file: Загружаемый файл
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
        s3: Клиент S3
    
    Returns:
        Информация о загруженном файле
    
    Raises:
        HTTPException: При ошибке загрузки
    """
    # Проверяем размер файла
    contents = await file.read()
    file_size = len(contents)
    
    max_size = settings.max_file_size_mb * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum of {settings.max_file_size_mb}MB"
        )
    
    # Генерируем ключ для S3
    user = await supabase.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    s3_key = s3.generate_key(
        user_id=user.username,
        filename=file.filename,
        prefix="uploads"
    )
    
    # Загружаем в S3
    url = await s3.upload_bytes(
        data=contents,
        key=s3_key,
        content_type=file.content_type or "application/octet-stream"
    )
    
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file to storage"
        )
    
    # Регистрируем в БД
    stored_file = await supabase.register_file(
        user_id=user_id,
        filename=file.filename,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=file_size,
        storage_path=s3_key,
        source_type="user_upload"
    )
    
    if not stored_file:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register file in database"
        )
    
    return FileUploadResponse(
        id=stored_file.id,
        filename=stored_file.filename,
        mime_type=stored_file.mime_type,
        size_bytes=stored_file.size_bytes,
        storage_path=stored_file.storage_path,
        created_at=stored_file.created_at
    )


@router.get("/{file_id}", response_model=FileInfo)
async def get_file_info(
    file_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    supabase: SupabaseClient = Depends(),
    s3: S3Client = Depends()
):
    """
    Получить информацию о файле и ссылку для скачивания.
    
    Args:
        file_id: UUID файла
        user_id: ID текущего пользователя
        supabase: Клиент Supabase
        s3: Клиент S3
    
    Returns:
        Информация о файле
    
    Raises:
        HTTPException: Если файл не найден
    """
    stored_file = await supabase.get_file(file_id)
    
    if not stored_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    # Генерируем URL для доступа
    url = None
    if stored_file.storage_path:
        url = s3.get_url(stored_file.storage_path)
    elif stored_file.external_url:
        url = stored_file.external_url
    
    return FileInfo(
        id=stored_file.id,
        filename=stored_file.filename,
        mime_type=stored_file.mime_type,
        size_bytes=stored_file.size_bytes,
        source_type=stored_file.source_type,
        storage_path=stored_file.storage_path,
        external_url=url,
        created_at=stored_file.created_at
    )

