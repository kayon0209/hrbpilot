"""Stable data structures for the retrieval layer.

These types are the contract between:
  - the retriever (dense / sparse / hybrid)
  - the fusion stage (RRF)
  - downstream consumers (scenario orchestrators, citation binding)

Orchestrators still consume plain dicts (via ``to_dict()``) so their response
shape and citation mapping stay unchanged; retrieval internals use typed
dataclasses for clarity and safety.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievedChunk:
    """A single retrieved document chunk, ranked by dense/sparse/hybrid."""

    chunk_id: str
    document_id: str
    kb_id: str
    source: str  # document filename
    section: str  # section heading / chunk label
    content: str  # chunk text
    score: float  # fusion score (hybrid) or native score (dense/sparse)
    confidence: float = 0.0  # calibrated evidence confidence in [0, 1]
    dense_rank: int | None = None  # 1-based rank in dense result (None if absent)
    sparse_rank: int | None = None  # 1-based rank in sparse result (None if absent)
    dense_score: float | None = None  # raw cosine similarity
    sparse_score: float | None = None  # raw ts_rank_cd

    def to_dict(self) -> dict[str, Any]:
        """Return the unified dict shape consumed by orchestrators/citations."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "kb_id": self.kb_id,
            "source": self.source,
            "section": self.section,
            "content": self.content,
            "score": self.score,
            "confidence": self.confidence,
            "dense_rank": self.dense_rank,
            "sparse_rank": self.sparse_rank,
            "dense_score": self.dense_score,
            "sparse_score": self.sparse_score,
        }


@dataclass
class RetrievalResult:
    """Result of a retrieval pass (single strategy or fused)."""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    strategy: str = "hybrid"  # dense | sparse | hybrid

    def to_dicts(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.chunks]

    def __bool__(self) -> bool:
        return bool(self.chunks)

    def __len__(self) -> int:
        return len(self.chunks)
