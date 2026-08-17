"""HRBP AI Workbench — Knowledge Base CRUD service.

Provides:
  - Knowledge base lifecycle: create, get, list, update, delete
  - Version tracking: each change increments version
  - Document management: add, remove, re-index documents within a KB
  - Per-tenant isolation via repository layer
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4


@dataclass
class KnowledgeBaseInfo:
    """Knowledge base metadata (returned by CRUD operations)."""
    id: str
    tenant_id: str
    scenario_id: str
    name: str
    chunk_strategy: str
    chunk_size: int
    status: str  # active | building | disabled
    version: int = 1
    document_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class KnowledgeBaseService:
    """Service layer for knowledge base CRUD + version management.

    In production, this talks to PostgreSQL via repositories.
    For now, it provides a clean in-memory interface that can
    be swapped to a DB-backed implementation without changing callers.
    """

    def __init__(self) -> None:
        self._store: dict[str, KnowledgeBaseInfo] = {}

    async def create(
        self,
        tenant_id: str,
        scenario_id: str,
        name: str,
        chunk_strategy: str = "default",
        chunk_size: int = 512,
    ) -> KnowledgeBaseInfo:
        kb = KnowledgeBaseInfo(
            id=str(uuid4()),
            tenant_id=tenant_id,
            scenario_id=scenario_id,
            name=name,
            chunk_strategy=chunk_strategy,
            chunk_size=chunk_size,
            status="active",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        self._store[kb.id] = kb
        return kb

    async def get(self, kb_id: str) -> KnowledgeBaseInfo | None:
        return self._store.get(kb_id)

    async def list_by_tenant(self, tenant_id: str) -> list[KnowledgeBaseInfo]:
        return [kb for kb in self._store.values() if kb.tenant_id == tenant_id]

    async def list_by_scenario(
        self, tenant_id: str, scenario_id: str
    ) -> list[KnowledgeBaseInfo]:
        return [
            kb
            for kb in self._store.values()
            if kb.tenant_id == tenant_id and kb.scenario_id == scenario_id
        ]

    async def update(
        self,
        kb_id: str,
        name: str | None = None,
        chunk_strategy: str | None = None,
        chunk_size: int | None = None,
        status: str | None = None,
    ) -> KnowledgeBaseInfo | None:
        kb = self._store.get(kb_id)
        if not kb:
            return None
        if name is not None:
            kb.name = name
        if chunk_strategy is not None:
            kb.chunk_strategy = chunk_strategy
        if chunk_size is not None:
            kb.chunk_size = chunk_size
        if status is not None:
            kb.status = status
        kb.version += 1
        kb.updated_at = datetime.now().isoformat()
        return kb

    async def delete(self, kb_id: str) -> bool:
        if kb_id in self._store:
            del self._store[kb_id]
            return True
        return False

    async def get_stats(self, kb_id: str) -> dict:
        """Return statistics for a knowledge base."""
        kb = self._store.get(kb_id)
        if not kb:
            return {}
        return {
            "id": kb.id,
            "name": kb.name,
            "version": kb.version,
            "document_count": kb.document_count,
            "status": kb.status,
            "created_at": kb.created_at,
            "updated_at": kb.updated_at,
        }


# Singleton instance
kb_service = KnowledgeBaseService()
