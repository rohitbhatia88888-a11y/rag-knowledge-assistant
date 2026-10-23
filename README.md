# Enterprise RAG Knowledge Assistant

A chat assistant over company documents that answers in plain language, cites the exact page and passage behind every answer, and only reads chunks the asking user is allowed to see.

## Architecture

```
Next.js chat ──► FastAPI ──► query rewrite ──► hybrid search (dense + BM25, ACL filter) ──► cross-encoder rerank ──► LLM (cited, streamed)
   │ SSO (OIDC)    │                                    ▲
                   │ upload                             │
                   ▼                                    │
           Redis queue ──► ingest worker: parse ─► chunk ─► embed (only changed chunks) ─► Qdrant
                ▲                                                         Postgres: documents, ACLs, connectors, chats, feedback
   cron (10 min)┘──► Google Drive sync: changes feed ─► download changed files ─► same pipeline; sharing ─► ACL
```

| Concern | How |
|---|---|
| Parsing | PyMuPDF with font-size heading detection and table extraction to Markdown; Markdown/text natively |
| Chunking | Heading-aware; each chunk carries its heading path, page span and a content hash; tables never split mid-row |
| Hybrid search | One Qdrant collection with a dense vector (BGE via fastembed, or OpenAI) and a sparse BM25 vector (IDF computed by Qdrant), fused with RRF |
| Permissions | `tenant_id` + `acl` on every chunk payload, filtered **inside** both search branches. ACL changes update payloads only, with no re-embedding. Fails closed. |
| Incremental re-index | Point IDs derive from `document_id + content_hash`; re-indexing embeds only new chunks and deletes stale ones |
| Re-ranking | Cross-encoder via fastembed (`ms-marco-MiniLM-L-12-v2`; it separated answerable from unanswerable questions better than `jina-reranker-v1-turbo` and `bge-reranker-v2-m3` on the samples). Section headings are included in what it scores. At most 3 of the top 5 chunks come from one document, so cross-document questions keep room for the second source |
| "I don't know" | If the top chunk scores below `MIN_RERANK_SCORE`, the LLM isn’t called; the prompt also requires the exact refusal sentence |
| Citations | Numbered sources, `[n]` markers validated after generation; invalid ones are logged |
| Feedback | Thumbs up/down stored against the assistant message, which records the search query and cited chunk IDs |
| Sign-in | OpenID Connect (authorization code + PKCE) with any IdP; email domain maps to a tenant; groups from the IdP's `groups` claim. Session JWTs expire after 8 hours |
| Connectors | Google Drive: full crawl, then the Drive changes feed every 10 minutes and a full reconcile daily. Drive sharing becomes the document ACL |
| Schema | Alembic migrations, run at startup by the API and worker under a Postgres advisory lock |

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

Open http://localhost:3000, sign in as `alice` with group `hr` (add `admins` to manage connectors), then upload `samples/*.md` with different "Visible to" settings. Sign in as `bob` with no groups to see the permission filter at work.

Or run everything in containers: `docker-compose up --build`. With Colima, give the VM at least 6 GB (`colima start --cpu 4 --memory 6`); the default 2 GB gets the API container OOM-killed, especially alongside Langfuse.

Without `OPENAI_API_KEY` the chat still runs retrieval and shows the cited passages, then reports that no LLM is configured. Any OpenAI-compatible endpoint works; `.env.example` is set up for OpenRouter.

## Single sign-on

Set these in `.env` (Google shown; Okta, Entra ID and Keycloak work the same way):

```
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=...apps.googleusercontent.com
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URI=http://localhost:8000/auth/oidc/callback
OIDC_DOMAIN_TENANTS={"yourcompany.com": "yourcompany"}
```

In Google Cloud Console → APIs & Services → Credentials, create an OAuth client ID of type *Web application* with that redirect URI. Sign-ins are refused unless the email is verified and its domain is listed in `OIDC_DOMAIN_TENANTS`. The `admins` group (from the IdP's groups claim, or the dev sign-in) can manage connectors. Google doesn't put groups in ID tokens, so Google sign-ins have no groups until a Directory API lookup is added; files shared with a Google group stay invisible to them (fail closed).

The flow keeps no server-side state: state, nonce and the PKCE verifier travel in a 10-minute signed cookie, and the session token returns in the URL fragment so it never reaches server logs. `/auth/dev-token` exists only when `ENV=dev`.

## Google Drive connector

1. In Google Cloud Console, enable the **Google Drive** and **IAM Service Account Credentials** APIs and create a **service account** (no project roles needed).
2. Authenticate without a key file (works where the org policy `iam.disableServiceAccountKeyCreation` blocks keys, the default for new organizations): run `gcloud auth application-default login`, grant yourself `roles/iam.serviceAccountTokenCreator` on the service account, and set `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT=<its email>`. The app then gets one-hour tokens for the service account. In production the same works from a workload identity instead of a user login. A key file (`GOOGLE_SERVICE_ACCOUNT_FILE`) is still supported, and is required for Workspace domain-wide delegation (`subject` on the connector).
3. Share the folders with the service account's email **as Editor**, added by email under *People with access*. Drive hides a file's sharing list from Viewers, and without it there is no ACL, so files are skipped (the connector shows this). The token only carries the `drive.readonly` scope, so the service account still cannot change anything. Access through "Anyone with the link" doesn't count: the service account can open the folder by ID but is never an editor.
4. Sign in as an admin, open **Connectors**, and add the folder IDs (the last part of the folder's URL). The first sync is a full crawl.

How syncing works (`backend/app/connectors/gdrive/sync.py`):

| Event in Drive | Effect |
|---|---|
| File added or edited (md5 / modifiedTime changed) | Downloaded and re-indexed; only changed chunks are re-embedded |
| Sharing, rename or move within scope | Chunk payloads updated; nothing is downloaded or re-embedded |
| Trashed, deleted, unshared from the connector, moved out of scope | Removed from the index |
| Folder moved into scope | Crawled, since Drive emits no change per file inside it |
| Folder trashed or moved out | Its files are removed unless they also live in another in-scope folder |

Sharing maps to principals: user → `user:<email>`, group → `group:<email>`, domain → `domain:<domain>` (matches every user with that email domain), anyone → `group:everyone` (within the tenant). PDFs, Markdown, text and Google Docs (exported as Markdown) are indexed; other types are skipped. The changes page token is saved only after a sync succeeds, so a failed sync is retried from the same point.

Tested live against a real Drive folder with keyless impersonation: first full sync indexed both Google Docs; an incremental sync then picked up an edit (re-indexed), a trashed file (removed) and the folder going from link-shared to Restricted (`group:everyone` dropped, so another user's question got "I don't know"). The Drive sync tests run against an in-memory fake Drive with a real changes feed (`tests/test_gdrive_sync.py`); SSO is tested against a fake IdP signing real RS256 tokens (`tests/test_oidc.py`) and was checked end to end against `ghcr.io/navikt/mock-oauth2-server`.

## Tracing (Langfuse)

```bash
docker-compose -f docker-compose.langfuse.yml -p langfuse up -d
```

Then set `LANGFUSE_PUBLIC_KEY=pk-lf-local-dev`, `LANGFUSE_SECRET_KEY=sk-lf-local-dev` and `LANGFUSE_BASE_URL=http://localhost:3001` in `.env`. The UI is at http://localhost:3001 (`admin@halcyon.example` / `local-dev-password`, local only).

Each chat request is one trace: `chat` → `rewrite-query` → `retrieve` (chunks, pages, re-ranker scores) → `answer`, with the LLM call recorded as a generation (model, tokens, cost). The trace carries tenant/user and session, and thumbs up/down from the UI lands on it as a `user_feedback` score, so weak answers can be filtered to their exact retrieval. Tracing is a no-op when the keys are unset.

## Tests

```bash
cd backend && uv run pytest
```

The tests run the real embedding and re-ranking models against an in-memory Qdrant. They cover tenant and ACL isolation, ACL changes without re-embedding, re-indexing only changed chunks, and the per-document chunk cap.

## Evaluation

`eval/corpus/` is a fictional company, Halcyon Robotics: 11 documents (Markdown, plus PDFs rendered from HTML so tables and page layout go through the real PDF parser) shared with four permission groups. `eval/dataset.jsonl` has 141 hand-written questions, each asked as a persona with that persona's groups:

| Category | n | What it tests |
|---|---|---|
| fact | 76 | single passage |
| table | 21 | answer inside a PDF or Markdown table |
| paraphrase | 9 | question uses different words than the document |
| multi_doc | 10 | needs passages from two documents |
| unanswerable | 15 | nothing in the corpus answers it |
| forbidden | 10 | the answer exists, but only in a document the persona can't see |

Every answerable question lists its evidence (document + phrase), so retrieval is scored without an LLM. The runner also verifies that every evidence phrase exists in the parsed corpus before running.

```bash
cd backend
uv run --group eval python ../eval/run_eval.py              # retrieval + gate: free, ~30 s
uv run --group eval python ../eval/run_eval.py --llm        # + answers, refusals, citations, RAGAS
uv run --group eval python ../eval/run_eval.py --gate       # exit 1 below eval/thresholds.json
uv run --group eval python ../eval/tune.py                  # compare configs, sweep the IDK threshold
```

| Metric | Meaning |
|---|---|
| `retrieval.hit_rate` / `evidence_recall` / `mrr` | all / each evidence passage in the top 5, and how high the first one ranks |
| `security.leaked_chunks` | chunks retrieved from a document the persona can't access (must be 0) |
| `gate.answer_rate` | answerable questions not cut off by the re-ranker score gate |
| `answer.answer_rate` / `refusal_rate` | final answers vs "I don't know" on answerable / unanswerable+forbidden |
| `answer.citation_rate`, `invalid_citations` | answers carrying `[n]` citations; citations to sources that don't exist |
| `ragas.faithfulness`, `ragas.answer_relevancy` | RAGAS 0.4, judged through the same OpenAI-compatible endpoint; relevancy embeddings run locally |

With tracing on, every eval question is a trace tagged `eval` with its RAGAS scores attached.

### First tuning pass (retrieval, 141 questions)

| Config | hit rate | evidence recall | MRR |
|---|---|---|---|
| Baseline: 1200-char chunks, bge-small, MiniLM-L-12 | 0.991 | 0.992 | 0.968 |
| 600 / 2000-char chunks | 0.991 | 0.992 | 0.968 |
| Re-ranker MiniLM-L-6 | 0.991 | 0.992 | 0.953 |
| Re-ranker bge-reranker-v2-m3 (int8) | 0.991 | 0.992 | 0.960 |
| Embeddings bge-base | 1.000 | 1.000 | 0.968 |
| final_k 8 | 1.000 | 1.000 | 0.968 |
| **Max 3 chunks per document (adopted)** | **1.000** | **1.000** | **0.968** |
| Max 2 chunks per document | 0.974 | 0.976 | 0.966 |

- The one miss was a cross-document question where all five slots went to one document. Capping chunks per document fixes it with no model change and no extra tokens.
- Chunk size made no difference: the corpus's sections are shorter than 600 characters, so heading-aware chunking decides boundaries. Re-test on longer documents.
- The re-ranker score is a weak "is this answerable?" signal: "pet insurance" scores +3.1 against the health-insurance chunk. The gate is now −11 (keeps 100% of answerable questions, skips the clearly off-topic 12%) and the LLM's refusal handles the rest.

### Answer quality (all 141 questions, Claude Haiku 5.5 as model and judge)

| Metric | Result |
|---|---|
| Answerable questions answered | 116 / 116 (1.00) |
| Unanswerable + forbidden questions refused | 25 / 25 (1.00) |
| Answers with citations / invalid citations | 1.00 / 0 |
| RAGAS faithfulness | 0.957 |
| RAGAS answer relevancy | 0.870 |
| Cost per full run | about $0.16 |

- The LLM refused all 22 unanswerable or forbidden questions that the −11 score gate let through, so loosening the gate cost no correctness.
- RAGAS with a small judge is noisy. Haiku scored the same 15 answers 0.62, then 0.75 on a re-run; Claude Sonnet 5.5 scored them 0.87. Answers like "The on-call handoff happens every Monday at 10:00 Pacific time [1]" (verbatim from the runbook) scored 0.5.
- Part of that was the `[1]` markers, which the judge treated as unsupported claims. Stripping them before scoring raised faithfulness on those 15 from 0.75 to 0.83, and on the full set from 0.947 to 0.957.
- CI keeps Haiku as judge (Sonnet costs about 20× more per run) and sets thresholds about 0.02 below the baseline to absorb judge variance; see `eval/thresholds.json`.

### CI

`.github/workflows/ci.yml` runs on every push and PR: unit tests, the retrieval eval with `--gate`, and the web typecheck/build. On PRs, if the `OPENAI_API_KEY` secret is set (plus optional `OPENAI_BASE_URL` / `LLM_MODEL` variables), it also runs the `--llm` eval and gates on faithfulness, relevancy, refusals and citations. Result files are uploaded as artifacts.

## Roadmap (Industry edition)

1. ~~Core pipeline: upload, parse, chunk, hybrid search, rerank, cited streaming, feedback~~
2. ~~Eval and tracing: 141-question set, RAGAS faithfulness/relevancy gated in CI, Langfuse traces~~
3. ~~Google Drive connector: changes API with page tokens, deletes, permission sync into `acl`, SSO (OIDC); Alembic migrations~~ (next: Google group expansion via the Directory API, push notifications instead of polling)
4. Notion and Confluence connectors; large tenants on their own Qdrant shard keys
5. Semantic cache keyed on question embedding **and** ACL fingerprint, invalidated on document version; load tests for p95
6. Agentic multi-hop retrieval for cross-document questions; weak-answer dashboard from feedback
