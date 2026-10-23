from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"  # "dev" enables /auth/dev-token

    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "chunks"
    upload_dir: str = "./data/uploads"

    # Auth: HS256 JWTs for now; swap for OIDC (Google / Entra) verification later.
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"

    rate_limit_per_minute: int = 30

    # Dense embeddings: "fastembed" runs a BGE model on CPU, "openai" calls the API.
    embedding_provider: str = "fastembed"
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    openai_embedding_model: str = "text-embedding-3-small"
    sparse_model: str = "Qdrant/bm25"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    llm_model: str = "gpt-4.1-mini"

    # Chunking
    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 150

    # Retrieval
    candidate_k: int = 40  # per-branch candidates before fusion
    fused_k: int = 20  # sent to the re-ranker
    final_k: int = 5  # sent to the LLM
    # Cross-encoder logit below which a chunk is treated as irrelevant.
    min_rerank_score: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
