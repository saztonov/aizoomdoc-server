"""
Сервис агента - оркестратор пайплайна обработки запросов.
"""

import logging
from typing import Optional, AsyncGenerator, Dict, Any
from uuid import UUID
from datetime import datetime

from app.models.internal import UserWithSettings, SearchResult, LLMResponse
from app.models.api import StreamEvent, PhaseStartedEvent, PhaseProgressEvent, LLMTokenEvent
from app.db.supabase_client import SupabaseClient
from app.db.supabase_projects_client import SupabaseProjectsClient
from app.db.s3_client import S3Client
from app.services.llm_service import create_llm_service
from app.services.search_service import SearchService
from app.services.image_service import ImageService

logger = logging.getLogger(__name__)


class AgentService:
    """Сервис агента для обработки запросов пользователя."""
    
    def __init__(
        self,
        user: UserWithSettings,
        supabase: SupabaseClient,
        projects_db: SupabaseProjectsClient,
        s3_client: S3Client
    ):
        """
        Инициализация сервиса агента.
        
        Args:
            user: Пользователь с настройками
            supabase: Клиент основной БД
            projects_db: Клиент Projects DB
            s3_client: Клиент S3
        """
        self.user = user
        self.supabase = supabase
        self.projects_db = projects_db
        self.s3_client = s3_client
        
        # Инициализация сервисов
        self.llm_service = create_llm_service(user)
        self.search_service = SearchService(projects_db)
        self.image_service = ImageService(s3_client)
    
    async def process_message(
        self,
        chat_id: UUID,
        user_message: str,
        client_id: str
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Обработать сообщение пользователя с стримингом событий.
        
        Пайплайн:
        1. Поиск в документах (search)
        2. Сбор контекста (processing)
        3. Генерация ответа LLM (llm)
        4. Обработка tool calls (zoom, request_images)
        5. Финальный ответ
        
        Args:
            chat_id: ID чата
            user_message: Сообщение пользователя
            client_id: ID клиента для поиска документов
        
        Yields:
            События стриминга
        """
        try:
            # Сохраняем сообщение пользователя в БД
            await self.supabase.add_message(
                chat_id=chat_id,
                role="user",
                content=user_message
            )
            
            # Фаза 1: Поиск в документах
            yield self._create_phase_event("search", "Поиск в документах...")
            
            search_result = await self.search_service.search_in_documents(
                query=user_message,
                client_id=client_id
            )
            
            yield self._create_progress_event(
                "search",
                1.0,
                f"Найдено {search_result.total_blocks_found} блоков"
            )
            
            # Фаза 2: Обработка и сбор контекста
            yield self._create_phase_event("processing", "Подготовка контекста...")
            
            context_text = self._format_search_context(search_result)
            
            yield self._create_progress_event("processing", 1.0, "Контекст подготовлен")
            
            # Фаза 3: Генерация ответа
            yield self._create_phase_event("llm", "Генерация ответа...")
            
            # Выбор режима (simple или complex)
            if self.user.settings.model_profile == "simple":
                async for event in self._process_simple_mode(
                    chat_id, user_message, context_text
                ):
                    yield event
            else:  # complex
                async for event in self._process_complex_mode(
                    chat_id, user_message, context_text, client_id
                ):
                    yield event
            
            # Завершение
            yield StreamEvent(
                event="completed",
                data={"message": "Обработка завершена"},
                timestamp=datetime.utcnow()
            )
        
        except Exception as e:
            logger.error(f"Error in process_message: {e}", exc_info=True)
            yield StreamEvent(
                event="error",
                data={"message": str(e)},
                timestamp=datetime.utcnow()
            )
    
    async def _process_simple_mode(
        self,
        chat_id: UUID,
        user_message: str,
        context_text: str
    ) -> AsyncGenerator[StreamEvent, None]:
        """Обработка в simple (flash) режиме."""
        
        # Загружаем системные промпты
        system_prompt = await self.llm_service.load_system_prompts(self.supabase)
        
        # Формируем полный промпт с контекстом
        full_message = f"{context_text}\n\nЗАПРОС ПОЛЬЗОВАТЕЛЯ: {user_message}"
        
        # Стримим ответ
        accumulated_response = ""
        
        async for token in self.llm_service.generate_simple(
            user_message=full_message,
            system_prompt=system_prompt
        ):
            accumulated_response += token
            
            yield StreamEvent(
                event="llm_token",
                data=LLMTokenEvent(
                    token=token,
                    accumulated=accumulated_response
                ).dict(),
                timestamp=datetime.utcnow()
            )
        
        # Сохраняем ответ в БД
        await self.supabase.add_message(
            chat_id=chat_id,
            role="assistant",
            content=accumulated_response
        )
        
        yield StreamEvent(
            event="llm_final",
            data={"content": accumulated_response},
            timestamp=datetime.utcnow()
        )
    
    async def _process_complex_mode(
        self,
        chat_id: UUID,
        user_message: str,
        context_text: str,
        client_id: str
    ) -> AsyncGenerator[StreamEvent, None]:
        """Обработка в complex (flash+pro) режиме."""
        
        # Этап 1: Flash собирает контекст
        yield self._create_phase_event("flash_stage", "Flash собирает контекст...")
        
        flash_result = await self.llm_service.generate_complex_flash(
            user_message=user_message,
            document_context=context_text,
            supabase=self.supabase
        )
        
        # TODO: Обработка tool calls (request_images, zoom)
        
        yield self._create_progress_event("flash_stage", 1.0, "Контекст собран")
        
        # Этап 2: Pro генерирует ответ
        yield self._create_phase_event("pro_stage", "Pro формирует ответ...")
        
        # Формируем релевантный контекст
        relevant_context = self._format_relevant_context(flash_result)
        
        # Стримим ответ от Pro
        accumulated_response = ""
        
        async for token in self.llm_service.generate_complex_pro(
            user_message=user_message,
            relevant_context=relevant_context,
            images=[],  # TODO: Добавить изображения из flash_result
            supabase=self.supabase
        ):
            accumulated_response += token
            
            yield StreamEvent(
                event="llm_token",
                data=LLMTokenEvent(
                    token=token,
                    accumulated=accumulated_response
                ).dict(),
                timestamp=datetime.utcnow()
            )
        
        # Сохраняем ответ в БД
        await self.supabase.add_message(
            chat_id=chat_id,
            role="assistant",
            content=accumulated_response
        )
        
        yield StreamEvent(
            event="llm_final",
            data={"content": accumulated_response},
            timestamp=datetime.utcnow()
        )
    
    def _format_search_context(self, search_result: SearchResult) -> str:
        """Форматировать результаты поиска в текстовый контекст."""
        context = f"НАЙДЕННЫЙ ТЕКСТ:\n\n"
        
        for i, block in enumerate(search_result.text_blocks, 1):
            context += f"=== БЛОК {i} ===\n"
            if block.block_id:
                context += f"ID: {block.block_id}\n"
            if block.page:
                context += f"Страница: {block.page}\n"
            context += f"{block.text}\n\n"
        
        return context
    
    def _format_relevant_context(self, flash_result: Dict[str, Any]) -> str:
        """Форматировать релевантный контекст из Flash результата."""
        # TODO: Реализовать форматирование
        return ""
    
    def _create_phase_event(self, phase: str, description: str) -> StreamEvent:
        """Создать событие начала фазы."""
        return StreamEvent(
            event="phase_started",
            data=PhaseStartedEvent(
                phase=phase,
                description=description
            ).dict(),
            timestamp=datetime.utcnow()
        )
    
    def _create_progress_event(
        self,
        phase: str,
        progress: float,
        message: str
    ) -> StreamEvent:
        """Создать событие прогресса."""
        return StreamEvent(
            event="phase_progress",
            data=PhaseProgressEvent(
                phase=phase,
                progress=progress,
                message=message
            ).dict(),
            timestamp=datetime.utcnow()
        )

