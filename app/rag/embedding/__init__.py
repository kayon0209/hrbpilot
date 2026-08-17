"""Embedding client abstraction — cloud-only by design.

Any OpenAI-compatible embeddings endpoint works (SiliconFlow, Alibaba
DashScope, Jina, OpenAI, ...). There is intentionally no local-model fallback:
the retrieval layer must fail loudly rather than degrade to zero vectors,
which would pollute the index (see ingestion plan).

Configure via settings:
    EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIMENSION
"""

from __future__ import annotations

import math

from openai import AsyncOpenAI

from app.config.settings import settings
from app.shared.logger import get_logger

logger = get_logger(__name__)


class EmbeddingClient:
    """OpenAI-compatible embeddings client (cloud)."""

    def __init__(self, base_url: str, api_key: str, model: str, dimension: int) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts.

        Raises on any failure — never returns zero vectors. Dimension is
        validated against ``EMBEDDING_DIMENSION`` to guard the Milvus index.
        """
        if not texts:
            return []

        logger.info("embedding_call", model=self.model, count=len(texts))
        try:
            resp = await self._client.embeddings.create(model=self.model, input=texts)
        except Exception as e:
            logger.error("embedding_call_failed", model=self.model, error=str(e))
            raise

        embeddings = [item.embedding for item in resp.data]
        if len(embeddings) != len(texts):
            raise ValueError(f"Embedding count mismatch: requested {len(texts)}, received {len(embeddings)}")
        if any(len(v) != self.dimension for v in embeddings):
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dimension}, got {[len(v) for v in embeddings]}"
            )
        if any(
            not all(math.isfinite(value) for value in vector) or math.sqrt(sum(value * value for value in vector)) == 0
            for vector in embeddings
        ):
            raise ValueError("Embedding provider returned an invalid vector (non-finite or zero norm)")
        return embeddings


def get_embedder() -> EmbeddingClient:
    """Build the configured embedding client (cloud-only)."""
    api_key = settings.effective_embedding_api_key
    if not api_key:
        raise RuntimeError("No embedding API key configured. Set EMBEDDING_API_KEY (or LLM_API_KEY) in .env")
    return EmbeddingClient(
        base_url=settings.embedding_base_url,
        api_key=api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
