# Copilot instructions for this repo

## Topology and big picture
- FastAPI backend for document processing, hybrid search, and RAG summarization. Entrypoint: `app/main.py`.
- Data plane is Supabase (Postgres + Storage). Vector + FTS retrieval is done via Supabase RPCs and SQL functions, not in Python.
- RAG flow (server-side): semantic + keyword retrieval -> merge/weight -> rerank (CrossEncoder) -> hydrate content by `id` -> optional Assistant-powered summary.
 - Post-migration context: the UI can now call OpenAI Responses + File Search directly for chat. This backend remains useful for ingestion (OCR/parse PDFs) and optional hybrid retrieval/summarization workflows.

## Key modules
- API routers under `app/api/**`:
  - `file_ops/search_docs.py`: main hybrid search + summary endpoint at `POST /api/assistant/search_docs` (and legacy `/api/file_ops/search_docs`).
  - `file_ops/upload.py`, `extract_text_api.py`, `embed_api.py`: ingestion, OCR/extract, embedding helpers.
  - `gdrive_ops/`: Google Drive sync endpoints.
- Core services in `app/core/**`: `config.py` (env), `openai_client.py`, `supabase_client.py`, `logger.py`, `logging_config.py`.
- Background tasks: `app/workers/**` with `MainWorker` orchestrated from `/api/run-worker`.

## Retrieval details (what matters)
- Semantic: Supabase RPC `match_file_items_openai` using an embedding of the term/query.
- Keyword: Supabase RPC `match_file_items_fts` (HTTP call with service role token) using OR-joined quoted terms.
- Merge weighting is decided per-term via `_decide_weighting`; default favors semantic unless the query looks lexical.
- Rerank: `sentence-transformers` CrossEncoder `ms-marco-MiniLM-L-6-v2` on the top 24 passages.
- Hydration: fetched by `id` from `file_items` to get full `content` for summarization.

## Summary generation
- If `SEARCH_ASSISTANT_ID` is set, the service uses the OpenAI Assistants API to return JSON with `summary_markdown`, `used_source_labels`, `follow_up_questions`.
- If assistant output is missing or incomplete, the API responds with `fallback_text` and still returns ordered `sources`.
 - Considering migration to the OpenAI Responses API + File Search? Port persona to `instructions`, attach your `vector_store_id`, and keep the rerank/hydration stages. See `openai-cookbook/openai-responses-mini-cookbook.md` and `openai-cookbook/openai-vector-stores-guide.md`.

## Logging & debugging
- Structured logs with request IDs (`X-Request-ID`), set in middleware (`app/main.py`).
- Useful events to grep: `rag.embed`, `keyword.search.*`, `rag.rerank`, `rag.hydration.*`, `rag.summary.*`, `rag.final`, `rag.emit`.
- Configure via env: `LOG_LEVEL`, `LOG_JSON`, `DEBUG_VERBOSE_LOG_TEXTS`, `LOG_SUMMARY_TEXT`.

## Local dev & tests
- Python 3.11+. Create a venv and `pip install -r requirements.txt`.
- Run API: `uvicorn app.main:app --reload`.
- Workers: `python run_worker.py` (OCR/ingestion); `python run_gdrive_sync.py` (optional Drive).
- Tests: `pytest tests/`.

## Required environment
- Supabase: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE`, `SUPABASE_STORAGE_BUCKET`.
- OpenAI: `OPENAI_API_KEY`; optional `SEARCH_ASSISTANT_ID` to enable summarization.
- CORS: `ALLOWED_ORIGINS` (comma-separated); API prefix is `settings.API_PREFIX` (default `/api`).

## Environment status (as of 2025-10-31)
- `.env` is populated and git-ignored; values mirror Railway. Notable keys:
  - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE`, `SUPABASE_STORAGE_BUCKET` are set.
  - `OPENAI_API_KEY` is present; `SEARCH_ASSISTANT_ID` optional.
  - `PGVECTOR_CONN_STR` points to the remote instance.
  - `ALLOWED_ORIGINS` includes the Vercel URL and localhost.
- Do not check secrets into version control; rely on env files and platform envs (Railway/Vercel) for deployment.

## Contract expectations (examples)
- Input to `/api/assistant/search_docs`: `{ user_prompt, search_plan: { operator, terms }, [filters...] }`.
- Output fields relied on by the UI: `sources[]` with `file_name`, `excerpt`, `id`, `file_id`, optional `meeting_date`; plus `summary` or `fallback_text`.

## Integration touchpoints
- Frontend (`chatbot-ui`) may call this service for hybrid RAG. Keep this repo focused on retrieval/summary; don’t duplicate UI logic.
- Database-side logic lives in Supabase functions: `match_file_items_openai` (semantic) and `match_file_items_fts` (keyword); keep those performant and return only metadata, not full content.
 - If the UI’s File Search feature flag is ON, uploads go through `/api/vector-stores/upload` on the UI server and chat goes via `/api/chat/respond`; this service is optional for chat, but can still provide OCR/ingestion and Drive sync.

## Post-migration scope (if UI moves to Responses + File Search)
- Responsibility shifts to ingestion only: OCR/parse PDFs, optional cleanup/chunking, then upload to OpenAI Files and attach to a workspace Vector Store.
- Suggested endpoints:
  - `POST /api/ingest/upload` → accept files (and optionally Drive links), run OCR if needed, create OpenAI Files (`purpose:"assistants"`), attach to `vector_store_id`.
  - `GET /api/ingest/status/:id` → report OCR/indexing status; polling model (OpenAI has no ingestion webhooks).
  - (Optional) `DELETE /api/ingest/file/:id` → detach/delete from Vector Store when removed.
- Defer RAG/summary to the UI’s `/api/chat/respond` route using Responses API; deprecate `/api/assistant/search_docs`.
- Info needed: OCR engine choice (e.g., Tesseract), size limits and types, expected throughput, and how `vector_store_id` is discovered (passed from UI or looked up by workspace).
 - Project-specific: Use `gpt-5` as the target model; no org/project overrides needed. Reuse the existing OCR pipeline (see `app/api/file_ops/extract_text_api.py`, `app/api/file_ops/ocr.py`); iterate on quality/perf later.
 - UI alignment (current status): UI has implemented `/api/vector-stores/create`, `/api/vector-stores/upload`, and `/api/chat/respond`. Consider mirroring an ingestion-first `/api/ingest/upload` here for heavier files (OCR) and returning OpenAI File IDs to the UI.
  - Current role: With the UI’s new File Search flow, this service is optional for chat; keep it focused on OCR/ingestion and optional hybrid retrieval.

### Working order (backend, optional)
1. Keep OCR/ingestion modules as-is; expose thin endpoints:
  - `POST /api/ingest/upload` → OCR if needed, create OpenAI File(s), attach to `vector_store_id`.
  - `GET /api/ingest/status/:id` → simple polling for OCR/index readiness.
2. Accept `workspace_id` and resolve `vector_store_id` from the UI’s Supabase table.
3. Add logging around OCR duration, file size, attach success; avoid logging raw text.
4. Defer/retire `/api/assistant/search_docs` once UI is fully on `/api/chat/respond`.

### Agent loop (optional)
- If you add server-side tool orchestration (beyond OCR), use the Responses API tool loop patterns from `openai-cookbook/openai-agent-builder-guide.md`:
  - Declare function tools, execute model tool_calls, return results with `previous_response_id`.
  - Guard with small `MAX_STEPS`; prefer `response_format` for structured outputs.
