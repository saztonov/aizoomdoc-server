"""
Клиент для работы с S3/R2 хранилищем.
"""

import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4
import mimetypes

import boto3
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


class S3Client:
    """Клиент для работы с S3/R2 хранилищем."""
    
    def __init__(self):
        """Инициализация S3 клиента."""
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto"
        )
        self.bucket_name = settings.r2_bucket_name
        self.public_domain = settings.r2_public_domain
    
    def _get_public_url(self, key: str) -> str:
        """
        Получить публичный URL файла.
        
        Args:
            key: Ключ файла в S3
        
        Returns:
            Публичный URL
        """
        if self.public_domain:
            return f"{self.public_domain}/{key}"
        return f"{settings.r2_endpoint_url}/{self.bucket_name}/{key}"
    
    async def upload_file(
        self,
        file_path: str,
        key: str,
        content_type: Optional[str] = None
    ) -> Optional[str]:
        """
        Загрузить файл в S3.
        
        Args:
            file_path: Путь к локальному файлу
            key: Ключ файла в S3
            content_type: MIME тип файла (определяется автоматически если не указан)
        
        Returns:
            URL загруженного файла или None при ошибке
        """
        try:
            if content_type is None:
                content_type, _ = mimetypes.guess_type(file_path)
                if content_type is None:
                    content_type = "application/octet-stream"
            
            extra_args = {"ContentType": content_type}
            
            self.s3_client.upload_file(
                file_path,
                self.bucket_name,
                key,
                ExtraArgs=extra_args
            )
            
            url = self._get_public_url(key)
            logger.info(f"Uploaded file to S3: {key}")
            return url
        
        except ClientError as e:
            logger.error(f"Error uploading file to S3: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error uploading file: {e}")
            return None
    
    async def upload_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str = "application/octet-stream"
    ) -> Optional[str]:
        """
        Загрузить байты в S3.
        
        Args:
            data: Данные для загрузки
            key: Ключ файла в S3
            content_type: MIME тип файла
        
        Returns:
            URL загруженного файла или None при ошибке
        """
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type
            )
            
            url = self._get_public_url(key)
            logger.info(f"Uploaded bytes to S3: {key}")
            return url
        
        except ClientError as e:
            logger.error(f"Error uploading bytes to S3: {e}")
            return None
    
    async def download_file(self, key: str, local_path: str) -> bool:
        """
        Скачать файл из S3.
        
        Args:
            key: Ключ файла в S3
            local_path: Путь для сохранения файла
        
        Returns:
            True если успешно, False при ошибке
        """
        try:
            self.s3_client.download_file(
                self.bucket_name,
                key,
                local_path
            )
            
            logger.info(f"Downloaded file from S3: {key}")
            return True
        
        except ClientError as e:
            logger.error(f"Error downloading file from S3: {e}")
            return False
    
    async def download_bytes(self, key: str) -> Optional[bytes]:
        """
        Скачать файл из S3 как байты.
        
        Args:
            key: Ключ файла в S3
        
        Returns:
            Данные файла или None при ошибке
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=key
            )
            
            data = response["Body"].read()
            logger.info(f"Downloaded bytes from S3: {key}")
            return data
        
        except ClientError as e:
            logger.error(f"Error downloading bytes from S3: {e}")
            return None
    
    async def delete_file(self, key: str) -> bool:
        """
        Удалить файл из S3.
        
        Args:
            key: Ключ файла в S3
        
        Returns:
            True если успешно, False при ошибке
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=key
            )
            
            logger.info(f"Deleted file from S3: {key}")
            return True
        
        except ClientError as e:
            logger.error(f"Error deleting file from S3: {e}")
            return False
    
    async def file_exists(self, key: str) -> bool:
        """
        Проверить существование файла в S3.
        
        Args:
            key: Ключ файла в S3
        
        Returns:
            True если файл существует
        """
        try:
            self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=key
            )
            return True
        
        except ClientError:
            return False
    
    def generate_key(self, user_id: str, filename: str, prefix: str = "uploads") -> str:
        """
        Сгенерировать уникальный ключ для файла.
        
        Args:
            user_id: ID пользователя
            filename: Имя файла
            prefix: Префикс пути
        
        Returns:
            Ключ файла в S3
        """
        # Добавляем UUID для уникальности
        file_ext = Path(filename).suffix
        unique_name = f"{uuid4()}{file_ext}"
        
        return f"{prefix}/{user_id}/{unique_name}"
    
    def get_url(self, key: str) -> str:
        """
        Получить публичный URL файла.
        
        Args:
            key: Ключ файла в S3
        
        Returns:
            Публичный URL
        """
        return self._get_public_url(key)

