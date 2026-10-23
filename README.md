# Enterprise RAG Knowledge Assistant

A chat assistant over company documents that answers in plain language, cites the exact page and passage behind every answer, and only reads chunks the asking user is allowed to see.

## Architecture

```
Next.js chat ──► FastAPI ──► query rewrite ──► hybrid search (dense + BM25, ACL filter) ──► cross-encoder rerank ──► LLM (cited, streamed)
                   │                                    ▲
                   │ upload                             │
                   ▼                                    │
           Redis queue ──► ingest worker: parse ─► chunk ─► embed (only changed chunks) ─► Qdrant
                                                                          Postgres: documents, ACLs, chats, feedback
```

| Concern | How |
|---|---|
| Parsing | PyMuPDF with font-size heading detection and table extraction to Markdown; Markdown/text natively |
| Chunking | Heading-aware; each chunk carries its heading path, page span and a content hash; tables never split mid-row |
| Hybrid search | One Qdrant collection with a dense vector (BGE via fastembed, or OpenAI) and a sparse BM25 vector (IDF computed by Qdrant), fused with RRF |
| Permissions | `tenant_id` + `acl` on every chunk payload, filtered **inside** both search branches. ACL changes update payloads only, with no re-embedding. Fails closed. |
| Incremental re-index | Point IDs derive from `document_id + content_hash`; re-indexing embeds only new chunks and deletes stale ones |
| Re-ranking | Cross-encoder via fastembed (`ms-marco-MiniLM-L-6-v2` by default, `bge-reranker-base` optional) |
| "I don't know" | If no chunk clears `MIN_RERANK_SCORE`, the LLM isn't called; the prompt also requires the exact refusal sentence |
| Citations | Numbered sources, `[n]` markers validated after generation; invalid ones are logged |
| Feedback | Thumbs up/down stored against the assistant message, which records the search query and cited chunk IDs |

## Run it

Needs Docker (Docker Desktop or Colima), [uv](https://docs.astral.sh/uv/) and Node 22.

```bash
cp .env.example .env    # add OPENAI_API_KEY for generated answers
docker-compose up -d postgres redis qdrant
```

```bash
cd backend && cp ../.env .env && uv sync && uv run uvicorn app.main:app --reload --port 8000
```

In a second terminal, start the ingest worker:

```bash
cd backend && uv run arq app.worker.WorkerSettings
```

```bash
cd web && npm install && npm run dev
```

Open http://localhost:3000, sign in as `alice` with group `hr`, then upload `samples/*.md` with different "Visible to" settings. Sign in as `bob` with no groups to see the permission filter at work.

Or run everything in containers: `docker-compose up --build`.

Without `OPENAI_API_KEY` the chat still runs retrieval and shows the cited passages, then reports that no LLM is configured.

## Tests and eval

```bash
cd backend && uv run pytest
```

The tests run the real embedding and re-ranking models against an in-memory Qdrant. They cover tenant and ACL isolation, ACL changes without re-embedding, and re-index embedding only changed chunks.

```bash
uv --project backend run python eval/retrieval_eval.py eval/sample_dataset.jsonl --user alice --groups hr
```

## Roadmap (Industry edition)

1. ~~Core pipeline: upload, parse, chunk, hybrid search, rerank, cited streaming, feedback~~
2. Eval and tracing: 100+ question set, RAGAS faithfulness/relevancy gated in CI, Langfuse traces
3. Google Drive connector: changes API with page tokens, deletes, permission sync into `acl`, SSO (OIDC)
4. Notion and Confluence connectors; Alembic migrations; large tenants on their own Qdrant shard keys
5. Semantic cache keyed on question embedding **and** ACL fingerprint, invalidated on document version; load tests for p95
6. Agentic multi-hop retrieval for cross-document questions; weak-answer dashboard from feedback
