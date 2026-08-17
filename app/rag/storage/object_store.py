"""MinIO object storage access layer.

The single place that reads/writes raw uploaded files. Everything else
(documents, chunks, Milvus) references files by their object key.
"""

from __future__ import annotations

import asyncio
import io

from minio import Minio

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)


class ObjectStore:
    """Synchronous minio client wrapped as async via ``asyncio.to_thread``."""

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        secure: bool | None = None,
    ) -> None:
        self.endpoint = endpoint or settings.minio_endpoint
        self.bucket = bucket or settings.minio_bucket
        self._client = Minio(
            self.endpoint,
            access_key=access_key or settings.minio_access_key,
            secret_key=secret_key or settings.minio_secret_key,
            secure=secure if secure is not None else settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)
            logger.info("minio_bucket_created", bucket=self.bucket)

    async def ensure_bucket_async(self) -> None:
        await asyncio.to_thread(self.ensure_bucket)

    def check_connection(self) -> None:
        self._client.bucket_exists(self.bucket)

    async def check_connection_async(self) -> None:
        await asyncio.to_thread(self.check_connection)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(
            self.bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    async def put_async(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(self.put, key, data, content_type)

    def get(self, key: str) -> bytes:
        response = self._client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def get_async(self, key: str) -> bytes:
        return await asyncio.to_thread(self.get, key)

    def delete(self, key: str) -> None:
        self._client.remove_object(self.bucket, key)

    async def delete_async(self, key: str) -> None:
        await asyncio.to_thread(self.delete, key)
