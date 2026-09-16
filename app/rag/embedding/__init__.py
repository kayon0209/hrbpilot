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

    async def aclose(self) -> None:
        """Close the underlying HTTP client (script shutdown hygiene)."""
        await self._client.close()


def get_embedder() -> EmbeddingClient:
    """Build the configured embedding client (cloud-only)."""
    api_key = settings.effective_embedding_api_key
    if not api_key:
        raise RuntimeError("No embedding API key configured. Set EMBEDDING_API_KEY (or LLM_API_KEY) in .env")
    if not settings.embedding_base_url.strip():
        # 空 base_url **不会**回落到 api.openai.com：实测 ``AsyncOpenAI(base_url="")``
        # 在请求时抛 ``APIConnectionError("Connection error.")``。那条错误在
        # ``Retriever._hybrid`` 里会被 ``return_exceptions=True`` 吞掉，dense 腿静默
        # 死亡、排序退化成纯关键词 —— 2026-09-16 事故的真实机制。
        #
        # 教训是：**模糊的错误比没有错误更糟**。原始报错看不出是配置缺失，于是只能从
        # "检索结果为什么不对"逆推回"少了一个环境变量"。这里把它变成指名道姓的失败。
        raise RuntimeError(
            "EMBEDDING_BASE_URL is empty. Set it to the OpenAI-compatible embeddings endpoint "
            "(for example https://api.siliconflow.cn/v1 or https://ai.gitee.com/v1). "
            "Without it every embed call fails with an opaque connection error, and the hybrid "
            "retriever silently degrades to keyword-only ranking."
        )
    return EmbeddingClient(
        base_url=settings.embedding_base_url,
        api_key=api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
