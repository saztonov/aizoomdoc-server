"""
Сервис для поиска в документах.
"""

import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from app.models.internal import SearchResult, TextBlock
from app.db.supabase_projects_client import SupabaseProjectsClient

logger = logging.getLogger(__name__)


class SearchService:
    """Сервис для поиска в документах."""
    
    def __init__(self, projects_db: SupabaseProjectsClient):
        """
        Инициализация сервиса поиска.
        
        Args:
            projects_db: Клиент Projects DB
        """
        self.projects_db = projects_db
    
    async def search_in_documents(
        self,
        query: str,
        client_id: str,
        document_ids: Optional[List[str]] = None
    ) -> SearchResult:
        """
        Поиск в документах.
        
        TODO: Портировать логику из src/search_engine.py
        - Поиск по markdown файлам (result.md)
        - Поиск по annotation.json
        - Поиск по ocr.html
        - Ранжирование результатов
        
        Args:
            query: Поисковый запрос
            client_id: ID клиента
            document_ids: Список ID документов для поиска (если None - по всем)
        
        Returns:
            Результаты поиска
        """
        text_blocks = []
        
        # TODO: Реализовать поиск
        # 1. Получить документы из projects_db
        # 2. Загрузить result.md, annotation.json, ocr.html для каждого
        # 3. Выполнить текстовый поиск
        # 4. Извлечь релевантные блоки
        # 5. Ранжировать результаты
        
        return SearchResult(
            text_blocks=text_blocks,
            images=[],
            query=query,
            total_blocks_found=len(text_blocks)
        )
    
    async def extract_context_from_block(
        self,
        block_id: str,
        document_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Извлечь полный контекст блока из документа.
        
        Args:
            block_id: ID блока
            document_id: ID документа
        
        Returns:
            Контекст блока (текст, изображения, координаты)
        """
        # TODO: Реализовать извлечение контекста
        # 1. Загрузить annotation.json
        # 2. Найти блок по ID
        # 3. Получить текст и метаданные
        # 4. Сформировать viewport если нужно
        
        return None

