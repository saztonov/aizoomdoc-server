"""
Сервис для асинхронного каскадного удаления чатов.

Обрабатывает очередь задач удаления в фоновом режиме:
- Удаляет файлы из R2 (chat_images)
- Удаляет локальные логи сервера
- Удаляет записи из БД (messages, chat_images, storage_files, chats)
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

from app.config import settings
from app.db.supabase_client import SupabaseClient
from app.db.s3_client import S3Client

logger = logging.getLogger(__name__)


class DeletionService:
    """Сервис асинхронного удаления чатов."""
    
    _instance: Optional["DeletionService"] = None
    
    def __init__(self):
        """Инициализация сервиса."""
        self._queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._supabase: Optional[SupabaseClient] = None
        self._s3: Optional[S3Client] = None
    
    @classmethod
    def get_instance(cls) -> "DeletionService":
        """Получить singleton экземпляр сервиса."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def start(self):
        """Запустить фонового воркера."""
        if self._running:
            return
        
        self._running = True
        self._supabase = SupabaseClient()
        self._s3 = S3Client()
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("DeletionService started")
    
    async def stop(self):
        """Остановить фонового воркера."""
        self._running = False
        
        if self._worker_task:
            # Добавляем None в очередь для разблокировки воркера
            await self._queue.put(None)  # type: ignore
            try:
                await asyncio.wait_for(self._worker_task, timeout=10.0)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
        
        logger.info("DeletionService stopped")
    
    def schedule_deletion(self, chat_id: UUID) -> None:
        """
        Запланировать удаление чата.
        
        Неблокирующий вызов - сразу добавляет в очередь и возвращает.
        
        Args:
            chat_id: UUID чата для удаления
        """
        asyncio.create_task(self._queue.put(chat_id))
        logger.info(f"Scheduled deletion for chat {chat_id}")
    
    async def _worker(self):
        """Фоновый воркер для обработки очереди удаления."""
        logger.info("Deletion worker started")
        
        while self._running:
            try:
                chat_id = await self._queue.get()
                
                # None - сигнал остановки
                if chat_id is None:
                    break
                
                await self._process_deletion(chat_id)
                self._queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in deletion worker: {e}", exc_info=True)
        
        logger.info("Deletion worker stopped")
    
    async def _process_deletion(self, chat_id: UUID):
        """
        Выполнить каскадное удаление чата.
        
        Порядок:
        1. Получить пути файлов из БД
        2. Удалить файлы из R2
        3. Удалить локальные логи
        4. Удалить записи из БД
        
        Args:
            chat_id: UUID чата
        """
        logger.info(f"Processing deletion for chat {chat_id}")
        
        try:
            # 1. Получить пути файлов из storage_files
            storage_paths = await self._get_storage_paths(chat_id)
            
            # 2. Удалить файлы из R2
            await self._delete_r2_files(storage_paths)
            
            # 3. Удалить локальные логи
            self._delete_local_logs(chat_id)
            
            # 4. Удалить записи из БД
            await self._delete_db_records(chat_id)
            
            logger.info(f"Successfully deleted chat {chat_id}")
            
        except Exception as e:
            logger.error(f"Error deleting chat {chat_id}: {e}", exc_info=True)
    
    async def _get_storage_paths(self, chat_id: UUID) -> list[str]:
        """Получить пути файлов в R2 для чата."""
        if not self._supabase:
            return []
        
        try:
            files = await self._supabase.get_chat_storage_files(chat_id)
            paths = []
            for f in files:
                storage_path = f.get("storage_path")
                if storage_path:
                    paths.append(storage_path)
            return paths
        except Exception as e:
            logger.error(f"Error getting storage paths for chat {chat_id}: {e}")
            return []
    
    async def _delete_r2_files(self, storage_paths: list[str]):
        """Удалить файлы из R2."""
        if not self._s3 or not storage_paths:
            return
        
        for path in storage_paths:
            try:
                await self._s3.delete_file(path)
                logger.debug(f"Deleted R2 file: {path}")
            except Exception as e:
                logger.warning(f"Failed to delete R2 file {path}: {e}")
    
    def _delete_local_logs(self, chat_id: UUID):
        """Удалить локальные лог-файлы сервера."""
        log_dir = Path(settings.llm_log_dir)
        log_file = log_dir / f"llm_dialog_{chat_id}.log"
        
        if log_file.exists():
            try:
                log_file.unlink()
                logger.debug(f"Deleted local log: {log_file}")
            except Exception as e:
                logger.warning(f"Failed to delete local log {log_file}: {e}")
    
    async def _delete_db_records(self, chat_id: UUID):
        """Удалить записи из БД в правильном порядке."""
        if not self._supabase:
            return
        
        try:
            await self._supabase.delete_chat_cascade(chat_id)
            logger.debug(f"Deleted DB records for chat {chat_id}")
        except Exception as e:
            logger.error(f"Error deleting DB records for chat {chat_id}: {e}")


# Глобальный экземпляр для использования в роутерах
deletion_service = DeletionService.get_instance()

