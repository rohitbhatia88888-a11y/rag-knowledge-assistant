"""Dense and sparse (BM25) embedders.

fastembed models run on CPU via ONNX and are loaded once per process. They are
synchronous, so async callers go through `asyncio.to_thread`.
"""

import asyncio
from functools import lru_cache

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import models

from app.config import get_settings


class DenseEmbedder:
    def __init__(self) -> None:
        s = get_settings()
        self.provider = s.embedding_provider
        if self.provider == "openai":
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url)
            self._model_name = s.openai_embedding_model
            self.dim = 1536 if "small" in self._model_name else 3072
        else:
            self._model = TextEmbedding(s.fastembed_model)
            self.dim = len(next(iter(self._model.embed(["dim probe"]))))

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "openai":
            out: list[list[float]] = []
            for i in range(0, len(texts), 256):
                resp = await self._client.embeddings.create(model=self._model_name, input=texts[i : i + 256])
                out.extend(d.embedding for d in resp.data)
            return out
        return await asyncio.to_thread(lambda: [v.tolist() for v in self._model.embed(texts, batch_size=32)])

    async def embed_query(self, text: str) -> list[float]:
        if self.provider == "openai":
            return (await self.embed_documents([text]))[0]
        # query_embed applies the model's query instruction prefix (BGE expects one)
        return await asyncio.to_thread(lambda: next(iter(self._model.query_embed(text))).tolist())


class SparseEmbedder:
    """BM25 term weights. IDF is applied by Qdrant (Modifier.IDF), so the index stays current as docs change."""

    def __init__(self) -> None:
        self._model = SparseTextEmbedding(get_settings().sparse_model)

    async def embed_documents(self, texts: list[str]) -> list[models.SparseVector]:
        def run():
            return [
                models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
                for e in self._model.embed(texts, batch_size=64)
            ]

        return await asyncio.to_thread(run)

    async def embed_query(self, text: str) -> models.SparseVector:
        def run():
            e = next(iter(self._model.query_embed(text)))
            return models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())

        return await asyncio.to_thread(run)


@lru_cache
def get_dense_embedder() -> DenseEmbedder:
    return DenseEmbedder()


@lru_cache
def get_sparse_embedder() -> SparseEmbedder:
    return SparseEmbedder()
