import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api import auth, chat, connectors, documents, feedback, oidc
from app.config import get_settings
from app.db import init_db
from app.retrieval.embeddings import get_dense_embedder
from app.services import get_store
from app.tracing import get_langfuse

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    get_langfuse()  # instrument the OpenAI SDK before any client is used
    await init_db()
    await get_store().ensure_collection(get_dense_embedder().dim)
    app.state.redis = Redis.from_url(s.redis_url)
    app.state.arq = await create_pool(RedisSettings.from_dsn(s.redis_url))
    yield
    await app.state.arq.aclose()
    await app.state.redis.aclose()
    get_langfuse().flush()


app = FastAPI(title="RAG Knowledge Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_url],
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (auth.router, oidc.router, documents.router, chat.router, feedback.router, connectors.router):
    app.include_router(r)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
