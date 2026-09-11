"""Milvus access layer — the ONLY place that talks to Milvus.

Responsibilities:
  - collection lifecycle (create if missing, verify vector dimension)
  - upsert / delete by document or kb
  - dense cosine search with tenant_id + kb_id scalar filtering

Vectors live only here; PostgreSQL keeps the auditable text + keyword index.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pymilvus import DataType, MilvusClient

from app.config.settings import settings
from app.shared.errors import ValidationError
from app.shared.logger import get_logger

logger = get_logger(__name__)

VECTOR_FIELD = "embedding"
_FILTER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def validate_filter_id(value: str) -> str:
    """Validate an identifier before interpolating it into a Milvus expression."""
    if _FILTER_ID_PATTERN.fullmatch(value) is None:
        raise ValidationError("Invalid Milvus filter identifier")
    return value


class MilvusStore:
    """Synchronous pymilvus client wrapped as async via ``asyncio.to_thread``."""

    def __init__(
        self,
        uri: str | None = None,
        collection_name: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.uri = uri or settings.milvus_endpoint
        self.collection_name = collection_name or settings.milvus_collection
        self.dim = dim if dim is not None else settings.embedding_dimension
        self._client: MilvusClient | None = None

    def _get_client(self) -> MilvusClient:
        if self._client is None:
            self._client = MilvusClient(uri=self.uri)
        return self._client

    # --- lifecycle ---

    def ensure_collection(self) -> None:
        """Create the collection if missing; verify dimension matches config."""
        client = self._get_client()
        if client.has_collection(self.collection_name):
            actual_dim = self._collection_dim(client)
            if actual_dim is not None and actual_dim != self.dim:
                raise RuntimeError(
                    f"Milvus collection '{self.collection_name}' vector dim {actual_dim} "
                    f"does not match EMBEDDING_DIMENSION={self.dim}"
                )
            return

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field(field_name="tenant_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="kb_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name=VECTOR_FIELD, datatype=DataType.FLOAT_VECTOR, dim=self.dim)

        index_params = client.prepare_index_params()
        index_params.add_index(field_name=VECTOR_FIELD, index_type="AUTOINDEX", metric_type="COSINE")

        client.create_collection(collection_name=self.collection_name, schema=schema, index_params=index_params)
        logger.info("milvus_collection_created", collection=self.collection_name, dim=self.dim)

    def _collection_dim(self, client: MilvusClient) -> int | None:
        try:
            info: dict[str, Any] = client.describe_collection(self.collection_name)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("milvus_describe_failed", error=str(e))
            return None
        for field in info.get("fields", []):
            if field.get("field_name") == VECTOR_FIELD:
                params = field.get("params") or field.get("type_params") or {}
                dim = params.get("dim")
                return int(dim) if dim else None
        return None

    async def ensure_collection_async(self) -> None:
        await asyncio.to_thread(self.ensure_collection)

    def check_connection(self) -> None:
        self._get_client().list_collections()

    async def check_connection_async(self) -> None:
        await asyncio.to_thread(self.check_connection)

    # --- writes ---

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        """Upsert rows: {chunk_id, tenant_id, kb_id, document_id, embedding}."""
        if not rows:
            return
        client = self._get_client()
        self.ensure_collection()
        client.upsert(collection_name=self.collection_name, data=rows)

    async def upsert_async(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self.upsert, rows)

    def flush(self) -> None:
        """Seal writes once a complete ingestion batch has finished."""
        client = self._get_client()
        if client.has_collection(self.collection_name):
            client.flush(self.collection_name)

    async def flush_async(self) -> None:
        await asyncio.to_thread(self.flush)

    def delete_by_document(self, document_id: str) -> int:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return 0
        safe_document_id = validate_filter_id(document_id)
        res = client.delete(
            collection_name=self.collection_name,
            filter=f'document_id == "{safe_document_id}"',
        )
        return int(res.get("delete_count", 0) or 0)

    def delete_by_kb(self, kb_id: str) -> int:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return 0
        safe_kb_id = validate_filter_id(kb_id)
        res = client.delete(
            collection_name=self.collection_name,
            filter=f'kb_id == "{safe_kb_id}"',
        )
        return int(res.get("delete_count", 0) or 0)

    def delete_by_ids(self, chunk_ids: list[str]) -> int:
        """Delete exact chunk ids, used for transaction compensation/version cleanup."""
        if not chunk_ids:
            return 0
        safe_ids = [validate_filter_id(chunk_id) for chunk_id in chunk_ids]
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return 0
        res = client.delete(collection_name=self.collection_name, ids=safe_ids)
        return int(res.get("delete_count", 0) or 0)

    async def delete_by_document_async(self, document_id: str) -> int:
        return await asyncio.to_thread(self.delete_by_document, document_id)

    async def delete_by_kb_async(self, kb_id: str) -> int:
        return await asyncio.to_thread(self.delete_by_kb, kb_id)

    async def delete_by_ids_async(self, chunk_ids: list[str]) -> int:
        return await asyncio.to_thread(self.delete_by_ids, chunk_ids)

    # --- search ---

    def search(
        self,
        query_vector: list[float],
        tenant_id: str,
        kb_id: str,
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Dense cosine search scoped to (tenant_id, kb_id). Returns (chunk_id, score)."""
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return []
        if not query_vector:
            return []

        safe_tenant_id = validate_filter_id(tenant_id)
        safe_kb_id = validate_filter_id(kb_id)
        filter_expr = f'tenant_id == "{safe_tenant_id}" && kb_id == "{safe_kb_id}"'
        results = client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            filter=filter_expr,
            limit=top_k,
            anns_field=VECTOR_FIELD,
            search_params={"metric_type": "COSINE", "params": {}},
            output_fields=["document_id"],
            # Indexing and retrieval happen back-to-back in ingestion checks and
            # interactive uploads.  Milvus' default bounded consistency can hide
            # a freshly flushed row for a short window, producing a false empty
            # retrieval.  Strong consistency makes the write visible before the
            # search is evaluated.
            consistency_level="Strong",
        )
        hits = results[0] if results else []
        out: list[tuple[str, float]] = []
        for hit in hits:
            out.append((str(hit.get("id", "")), float(hit.get("distance", 0.0))))
        return out

    async def search_async(
        self,
        query_vector: list[float],
        tenant_id: str,
        kb_id: str,
        top_k: int,
    ) -> list[tuple[str, float]]:
        return await asyncio.to_thread(self.search, query_vector, tenant_id, kb_id, top_k)
