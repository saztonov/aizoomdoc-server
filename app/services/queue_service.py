"""
Сервис очереди запросов для ограничения параллельных LLM-вызовов.

Использует asyncio.Semaphore для ограничения одновременных запросов
и предоставляет информацию о позиции в очереди клиентам.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, AsyncGenerator
from uuid import UUID, uuid4
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class QueuedRequest:
    """Запрос в очереди."""
    request_id: str
    chat_id: UUID
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None


@dataclass
class QueueStatus:
    """Статус очереди для клиента."""
    position: int
    estimated_wait_seconds: int
    active_requests: int
    queue_size: int


class QueueService:
    """
    Сервис управления очередью LLM-запросов.
    
    Ограничивает количество одновременных запросов к LLM
    и информирует клиентов о позиции в очереди.
    """
    
    _instance: Optional["QueueService"] = None
    
    def __init__(self):
        """Инициализация сервиса очереди."""
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._active_requests: dict[str, QueuedRequest] = {}
        self._waiting_requests: dict[str, QueuedRequest] = {}
        self._request_order: list[str] = []  # Порядок запросов в очереди
        self._lock = asyncio.Lock()
        self._avg_processing_time: float = 15.0  # Среднее время обработки в секундах
        self._completed_count: int = 0
        self._total_processing_time: float = 0.0
        self._is_running: bool = False
    
    @classmethod
    def get_instance(cls) -> "QueueService":
        """Получить singleton-экземпляр сервиса."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def start(self) -> None:
        """Запустить сервис очереди."""
        if self._is_running:
            return
        
        self._semaphore = asyncio.Semaphore(settings.queue_max_concurrent)
        self._is_running = True
        logger.info(
            f"QueueService started: max_concurrent={settings.queue_max_concurrent}, "
            f"max_size={settings.queue_max_size}, timeout={settings.queue_timeout_seconds}s"
        )
    
    async def stop(self) -> None:
        """Остановить сервис и дождаться завершения активных запросов."""
        if not self._is_running:
            return
        
        self._is_running = False
        
        # Ждём завершения активных запросов (с таймаутом)
        if self._active_requests:
            logger.info(f"Waiting for {len(self._active_requests)} active requests to complete...")
            timeout = 30  # секунд
            start = time.time()
            while self._active_requests and (time.time() - start) < timeout:
                await asyncio.sleep(0.5)
        
        if self._active_requests:
            logger.warning(f"Force stopping with {len(self._active_requests)} active requests")
        
        logger.info("QueueService stopped")
    
    def _get_queue_status_unlocked(self, request_id: str) -> QueueStatus:
        """Получить статус очереди (без блокировки, вызывать под lock)."""
        position = 0
        if request_id in self._waiting_requests:
            try:
                position = self._request_order.index(request_id) + 1
            except ValueError:
                position = len(self._request_order)
        
        estimated_wait = int(position * self._avg_processing_time)
        
        return QueueStatus(
            position=position,
            estimated_wait_seconds=estimated_wait,
            active_requests=len(self._active_requests),
            queue_size=len(self._waiting_requests)
        )
    
    async def get_queue_status(self, request_id: str) -> QueueStatus:
        """Получить статус очереди для запроса."""
        async with self._lock:
            return self._get_queue_status_unlocked(request_id)
    
    async def enqueue(self, chat_id: UUID) -> tuple[str, QueueStatus]:
        """
        Добавить запрос в очередь.
        
        Returns:
            Tuple[request_id, QueueStatus]
        
        Raises:
            RuntimeError: Если очередь переполнена
        """
        if not self._is_running:
            raise RuntimeError("QueueService is not running")
        
        async with self._lock:
            # Проверяем размер очереди
            if len(self._waiting_requests) >= settings.queue_max_size:
                raise RuntimeError(
                    f"Queue is full ({settings.queue_max_size} requests). "
                    "Please try again later."
                )
            
            request_id = str(uuid4())
            request = QueuedRequest(request_id=request_id, chat_id=chat_id)
            self._waiting_requests[request_id] = request
            self._request_order.append(request_id)
            
            status = self._get_queue_status_unlocked(request_id)
            logger.info(
                f"Request {request_id[:8]} enqueued for chat {chat_id}, "
                f"position={status.position}, queue_size={status.queue_size}"
            )
            
            return request_id, status
    
    async def acquire(self, request_id: str) -> bool:
        """
        Получить слот для обработки запроса.
        
        Блокирует до получения слота или таймаута.
        
        Returns:
            True если слот получен, False при таймауте
        """
        if self._semaphore is None:
            raise RuntimeError("QueueService is not started")
        
        try:
            # Ждём освобождения слота с таймаутом
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=settings.queue_timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(f"Request {request_id[:8]} timed out waiting for slot")
            await self._remove_request(request_id)
            return False
        
        # Перемещаем из waiting в active
        async with self._lock:
            request = self._waiting_requests.pop(request_id, None)
            if request:
                request.started_at = time.time()
                self._active_requests[request_id] = request
                if request_id in self._request_order:
                    self._request_order.remove(request_id)
                
                logger.info(
                    f"Request {request_id[:8]} acquired slot, "
                    f"active={len(self._active_requests)}"
                )
        
        return True
    
    async def release(self, request_id: str) -> None:
        """Освободить слот после обработки запроса."""
        if self._semaphore is None:
            return
        
        async with self._lock:
            request = self._active_requests.pop(request_id, None)
            if request and request.started_at:
                processing_time = time.time() - request.started_at
                self._total_processing_time += processing_time
                self._completed_count += 1
                # Обновляем среднее время (скользящее среднее)
                self._avg_processing_time = (
                    self._total_processing_time / self._completed_count
                )
                logger.info(
                    f"Request {request_id[:8]} completed in {processing_time:.1f}s, "
                    f"avg={self._avg_processing_time:.1f}s"
                )
        
        self._semaphore.release()
    
    async def _remove_request(self, request_id: str) -> None:
        """Удалить запрос из очереди (при отмене/таймауте)."""
        async with self._lock:
            self._waiting_requests.pop(request_id, None)
            self._active_requests.pop(request_id, None)
            if request_id in self._request_order:
                self._request_order.remove(request_id)
    
    async def cancel(self, request_id: str) -> None:
        """Отменить запрос."""
        await self._remove_request(request_id)
        logger.info(f"Request {request_id[:8]} cancelled")
    
    async def execute_with_queue(
        self,
        chat_id: UUID,
        processor: Callable[[], AsyncGenerator[Any, None]]
    ) -> AsyncGenerator[Any, None]:
        """
        Выполнить обработку с очередью.
        
        Автоматически управляет очередью и генерирует события статуса.
        
        Args:
            chat_id: ID чата
            processor: Async generator функция обработки
            
        Yields:
            События от processor + события очереди
        """
        request_id, initial_status = await self.enqueue(chat_id)
        
        try:
            # Отправляем начальный статус очереди
            if initial_status.position > 0:
                yield {
                    "event": "queue_position",
                    "data": {
                        "position": initial_status.position,
                        "estimated_wait_seconds": initial_status.estimated_wait_seconds,
                        "active_requests": initial_status.active_requests,
                        "queue_size": initial_status.queue_size,
                    },
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Периодически обновляем статус пока ждём слот
            acquire_task = asyncio.create_task(self.acquire(request_id))
            last_status_update = time.time()
            
            while not acquire_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(acquire_task),
                        timeout=2.0  # Обновляем статус каждые 2 секунды
                    )
                except asyncio.TimeoutError:
                    # Отправляем обновление статуса
                    if time.time() - last_status_update >= 2.0:
                        status = await self.get_queue_status(request_id)
                        if status.position > 0:
                            yield {
                                "event": "queue_position",
                                "data": {
                                    "position": status.position,
                                    "estimated_wait_seconds": status.estimated_wait_seconds,
                                    "active_requests": status.active_requests,
                                    "queue_size": status.queue_size,
                                },
                                "timestamp": datetime.utcnow().isoformat()
                            }
                        last_status_update = time.time()
            
            # Проверяем результат acquire
            if not acquire_task.result():
                yield {
                    "event": "error",
                    "data": {"message": "Request timed out in queue"},
                    "timestamp": datetime.utcnow().isoformat()
                }
                return
            
            # Отправляем событие начала обработки
            yield {
                "event": "processing_started",
                "data": {"request_id": request_id},
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Выполняем обработку
            async for event in processor():
                yield event
                
        except Exception as e:
            logger.error(f"Error processing request {request_id[:8]}: {e}")
            yield {
                "event": "error",
                "data": {"message": str(e)},
                "timestamp": datetime.utcnow().isoformat()
            }
        finally:
            await self.release(request_id)


# Глобальный экземпляр
queue_service = QueueService.get_instance()

