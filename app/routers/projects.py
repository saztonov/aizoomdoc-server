"""
API роутер для работы с деревом проектов (read-only).
"""

import logging
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query

from app.core.dependencies import get_current_user_id
from app.db.supabase_projects_client import SupabaseProjectsClient
from app.models.api import TreeNode, DocumentResults, FileInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/tree", response_model=List[TreeNode])
async def get_tree_nodes(
    client_id: Optional[str] = Query(None, description="ID клиента (опционально)"),
    parent_id: Optional[UUID] = Query(None, description="ID родительского узла (None для корневых)"),
    node_type: Optional[str] = Query(None, description="Тип узла (client, project, section, stage, task, document)"),
    all_nodes: bool = Query(False, description="Получить все узлы (игнорировать parent_id)"),
    include_files: bool = Query(False, description="Включить файлы результатов (MD, HTML) из job_files"),
    user_id: UUID = Depends(get_current_user_id),
    projects_db: SupabaseProjectsClient = Depends()
):
    """
    Получить узлы дерева проектов.

    Args:
        client_id: ID клиента
        parent_id: ID родительского узла
        node_type: Тип узла
        all_nodes: Получить все узлы дерева
        include_files: Включить файлы результатов (result_md, ocr_html) из job_files
        user_id: ID текущего пользователя
        projects_db: Клиент Projects DB

    Returns:
        Список узлов дерева
    """
    nodes = await projects_db.get_tree_nodes(
        client_id=client_id,
        parent_id=parent_id,
        node_type=node_type,
        all_nodes=all_nodes
    )

    # Если нужно включить файлы результатов
    if include_files and nodes:
        # Получить node_ids документов
        doc_node_ids = [
            UUID(node["id"]) if isinstance(node["id"], str) else node["id"]
            for node in nodes
            if node.get("node_type") == "document"
        ]

        if doc_node_ids:
            # Получить файлы из job_files
            files_map = await projects_db.get_job_files_for_nodes(doc_node_ids)

            # Добавить файлы к соответствующим узлам
            for node in nodes:
                node_id_str = str(node.get("id"))
                node["files"] = files_map.get(node_id_str, [])

    return [TreeNode(**node) for node in nodes]


@router.get("/documents/{document_id}/results", response_model=DocumentResults)
async def get_document_results(
    document_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    projects_db: SupabaseProjectsClient = Depends()
):
    """
    Получить файлы результатов обработки документа.
    
    Args:
        document_id: UUID узла документа
        user_id: ID текущего пользователя
        projects_db: Клиент Projects DB
    
    Returns:
        Файлы результатов (annotation, ocr_html, result_md, crops)
    
    Raises:
        HTTPException: Если документ не найден
    """
    # Проверяем существование документа
    node = await projects_db.get_node_by_id(document_id)
    
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    if node.get("node_type") != "document":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Node is not a document"
        )
    
    # Получаем файлы результатов
    files = await projects_db.get_document_results(document_id)
    
    return DocumentResults(
        document_node_id=document_id,
        files=[
            FileInfo(
                id=file["id"],
                filename=file["file_name"],
                mime_type=file.get("mime_type", "application/octet-stream"),
                size_bytes=file.get("file_size", 0),
                source_type=file["file_type"],
                storage_path=file["r2_key"],
                external_url=None,  # TODO: Генерировать URL для R2
                created_at=file["created_at"]
            )
            for file in files
        ]
    )


@router.get("/search", response_model=List[TreeNode])
async def search_documents(
    client_id: str = Query(..., description="ID клиента"),
    query: str = Query(..., description="Поисковый запрос"),
    limit: int = Query(20, description="Максимальное количество результатов", le=100),
    user_id: UUID = Depends(get_current_user_id),
    projects_db: SupabaseProjectsClient = Depends()
):
    """
    Поиск документов по имени или коду.
    
    Args:
        client_id: ID клиента
        query: Поисковый запрос
        limit: Максимальное количество результатов
        user_id: ID текущего пользователя
        projects_db: Клиент Projects DB
    
    Returns:
        Список найденных документов
    """
    documents = await projects_db.search_documents(
        client_id=client_id,
        query=query,
        limit=limit
    )
    
    return [TreeNode(**doc) for doc in documents]


