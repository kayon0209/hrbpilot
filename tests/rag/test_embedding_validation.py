import math
from types import SimpleNamespace

import pytest

from app.rag.embedding import EmbeddingClient


async def test_embedding_rejects_response_count_mismatch() -> None:
    client = EmbeddingClient("https://example.invalid/v1", "key", "model", 2)

    async def create(**_kwargs):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0])])

    client._client.embeddings.create = create
    with pytest.raises(ValueError, match="count mismatch"):
        await client.embed(["one", "two"])


@pytest.mark.parametrize("vector", [[0.0, 0.0], [math.nan, 1.0], [math.inf, 1.0]])
async def test_embedding_rejects_invalid_vector(vector) -> None:
    client = EmbeddingClient("https://example.invalid/v1", "key", "model", 2)

    async def create(**_kwargs):
        return SimpleNamespace(data=[SimpleNamespace(embedding=vector)])

    client._client.embeddings.create = create
    with pytest.raises(ValueError, match="invalid vector"):
        await client.embed(["one"])
