from functools import lru_cache

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from app.config import get_settings
from app.retrieval.store import VectorStore


@lru_cache
def get_store() -> VectorStore:
    s = get_settings()
    return VectorStore(AsyncQdrantClient(url=s.qdrant_url), s.qdrant_collection)


@lru_cache
def get_llm() -> AsyncOpenAI | None:
    s = get_settings()
    if not s.openai_api_key:
        return None
    return AsyncOpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url)
