"""
Главный файл FastAPI приложения.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import auth, user, prompts, chats, files, projects

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения."""
    from app.services.deletion_service import deletion_service
    from app.services.queue_service import queue_service
    
    logger.info("Starting AIZoomDoc Server...")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"CORS origins: {settings.cors_origins_list}")
    logger.info(f"Request queue: max_concurrent={settings.queue_max_concurrent}, max_size={settings.queue_max_size}")
    
    # Startup
    await deletion_service.start()
    await queue_service.start()
    
    yield
    
    # Shutdown
    await queue_service.stop()
    await deletion_service.stop()
    logger.info("Shutting down AIZoomDoc Server...")


# Создание приложения
app = FastAPI(
    title="AIZoomDoc Server",
    description="Backend API для анализа технической документации с помощью LLM",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключение роутеров
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(prompts.router)
app.include_router(chats.router)
app.include_router(files.router)
app.include_router(projects.router)


# Health check
@app.get("/health")
async def health_check():
    """Проверка состояния сервера."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "service": "aizoomdoc-server"
    }


# Root endpoint
@app.get("/")
async def root():
    """Корневой эндпоинт."""
    return {
        "service": "AIZoomDoc Server",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health"
    }


# Обработка ошибок
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Глобальный обработчик ошибок."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred",
            "details": str(exc) if settings.debug else None
        }
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )


