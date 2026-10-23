# Public demo: API + web app + pre-indexed Halcyon corpus in one container (Hugging Face Spaces).
# Build context is the repository root:  docker build -f deploy/huggingface/Dockerfile .

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Static export, served by the API on the same origin.
ENV NEXT_OUTPUT=export NEXT_PUBLIC_API_URL=""
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

# Spaces run the container as uid 1000.
RUN useradd -m -u 1000 user
WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/backend/.venv/bin:$PATH" \
    FASTEMBED_CACHE_PATH=/app/models \
    DEMO_MODE=true ENV=demo \
    DEMO_CORPUS_DIR=/app/eval/corpus \
    DATABASE_URL=sqlite+aiosqlite:////app/data/demo.db \
    QDRANT_PATH=/app/data/qdrant \
    UPLOAD_DIR=/app/data/uploads \
    WEB_STATIC_DIR=/app/web \
    WEB_URL=http://localhost:7860 \
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
    LLM_MODEL=anthropic/claude-haiku-5.5

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY eval/corpus /app/eval/corpus
RUN uv sync --frozen --no-dev
COPY --from=web /web/out /app/web

# Download the models and index the corpus at build time, so the Space starts without network fetches.
RUN mkdir -p /app/data /app/models && python -m app.demo seed && chown -R user:user /app

USER user
EXPOSE 7860
# JWT_SECRET and OPENAI_API_KEY come from the Space's secrets.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
