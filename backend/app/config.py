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
    session_ttl_hours: int = 8

    # SSO (OpenID Connect): Google, Okta, Entra ID, ... Disabled unless issuer and client ID are set.
    oidc_issuer: str | None = None  # e.g. https://accounts.google.com
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = "http://localhost:8000/auth/oidc/callback"
    # Sign-ins are accepted only from these email domains, mapped to a tenant: {"halcyon.com": "halcyon"}.
    oidc_domain_tenants: dict[str, str] = {}
    oidc_groups_claim: str = "groups"  # Okta/Entra can emit groups; Google cannot
    web_url: str = "http://localhost:3000"

    # Google Drive connector: a service account key file. Share folders with the service account's
    # email, or set a connector's `subject` to impersonate a Workspace user via domain-wide delegation.
    google_service_account_file: str | None = None
    connector_sync_minutes: int = 10
    connector_full_sync_hours: int = 24

    rate_limit_per_minute: int = 30

    # Tracing is off unless both keys are set.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "http://localhost:3001"

    # Dense embeddings: "fastembed" runs a BGE model on CPU, "openai" calls the API.
    embedding_provider: str = "fastembed"
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    openai_embedding_model: str = "text-embedding-3-small"
    sparse_model: str = "Qdrant/bm25"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-12-v2"

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    llm_model: str = "gpt-4.1-mini"
    # Always cap output: some gateways (OpenRouter) otherwise reserve the model's full limit against credits.
    answer_max_tokens: int = 1024

    # Chunking
    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 150

    # Retrieval
    candidate_k: int = 40  # per-branch candidates before fusion
    fused_k: int = 20  # sent to the re-ranker
    final_k: int = 5  # sent to the LLM
    # At most this many of final_k from one document (0 = no cap). 3 fixed the multi-doc misses in the eval; 2 hurt.
    max_chunks_per_doc: int = 3
    # If the best chunk's cross-encoder logit is below this, answer "I don't know" without calling the LLM.
    # The score is a weak answerability signal (eval/tune.py sweep): -11 keeps every answerable eval question
    # and only skips clearly off-topic ones; the LLM's own refusal handles the rest.
    min_rerank_score: float = -11.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
