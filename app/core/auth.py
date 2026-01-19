"""
Утилиты для работы с JWT токенами.
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID
import jwt
from fastapi import HTTPException, status

from app.config import settings


def create_access_token(user_id: UUID, expires_delta: Optional[timedelta] = None) -> str:
    """
    Создать access JWT token.
    
    Args:
        user_id: ID пользователя
        expires_delta: Время жизни токена (по умолчанию из настроек)
    
    Returns:
        JWT token
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    
    expire = datetime.utcnow() + expires_delta
    
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access"
    }
    
    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm
    )
    
    return token


def decode_access_token(token: str) -> dict:
    """
    Декодировать и проверить access token.
    
    Args:
        token: JWT token
    
    Returns:
        Payload токена
    
    Raises:
        HTTPException: При невалидном токене
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
        
        return payload
    
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )


def get_user_id_from_token(token: str) -> UUID:
    """
    Извлечь user_id из токена.
    
    Args:
        token: JWT token
    
    Returns:
        UUID пользователя
    """
    payload = decode_access_token(token)
    user_id_str = payload.get("sub")
    
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )
    
    try:
        return UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token"
        )


