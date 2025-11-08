# Copilot instructions for this repo

Note: For cross-repo flags, routes, and end-to-end flows, see the workspace integration guide: [../COPILOT-WORKSPACE.md](../COPILOT-WORKSPACE.md).

## Topology and big picture
- FastAPI backend focused on ingestion and Vector Store maintenance for the UI’s File Search flow. Entrypoint: `app/main.py`.
- Data plane is Supabase (Postgres + Storage). This service syncs Google Drive → Supabase and attaches files to an OpenAI Vector Store.
- Post-migration: Hybrid search/legacy chunk/embedding endpoints have been removed from routing; only `app/api/Responses/**` endpoints are exposed.

- Key routers under `app/api/Responses/**`:
  - Ingestion upload: `/responses/vector-store/ingest/upload`
  - Backlog ingest trigger: `/responses/vector-store/ingest`
  - GDrive sync trigger: `/responses/gdrive/sync`
  - List/Delete/Purge: `/responses/list`, `/responses/file/*`, `/responses/vector-store/purge`
- Core services in `app/core/**`: `config.py` (env), `openai_async_client.py` (centralized client), `supabase_client.py`, `logger.py`, `logging_config.py`.
- Background tasks: `app/workers/**` with `MainWorker` orchestrated from `/api/run-worker`. The main ingestion logic is in `run_gdrive_sync_responses.py`.

## Retrieval details
Retrieval is now performed by the frontend (`chatbot-ui`) via the OpenAI Responses API + File Search. This backend no longer exposes any search or retrieval endpoints. Its sole responsibility is ingestion and data synchronization.

## Document Profiling
- The ingestion worker (`vs_ingest_worker.py`) can automatically generate a profile for each document it processes.
- Using the OpenAI Responses API, it creates a summary, a list of keywords, and extracts named entities.
- This profile data is **not** stored in a separate table. Instead, it is written directly to columns on the `file_workspaces` table (`profile_summary`, `profile_keywords`, `profile_entities`) to provide a single, unified data source for the frontend researcher.
- The `document_profiles` table has been deprecated and removed.

## Local workspace references (authoritative code sources)
- The following sibling folders are available in this multi-root VS Code workspace. Use them as live references for APIs, examples, and implementation details while building or debugging this backend:
  - `openai-cookbook/` – practical guides and runnable examples for Responses API, File Search, Vector Stores, and agent loops (aligns with this service’s ingestion + VS ops).
  - `openai-python/` – Python SDK source and examples; the definitive place to confirm request/response shapes, retries, timeouts, and streaming helpers in Python.
  - `openai-node/` – Node/TypeScript SDK source and examples; helpful when ensuring API parity or comparing streaming patterns used by the frontend.
  - `evals/` – evaluation framework you can adapt for smoke tests or regression checks on summarization/retrieval behavior.

Notes
- These paths are local workspace references (not GitHub links). In VS Code, open the folders in the Explorer to navigate current code and docs.

## Logging & debugging
- Structured logs with request IDs (`X-Request-ID`), set in middleware (`app/main.py`).
- Useful events to grep for in Railway logs: `[responses.gdrive]`, `[responses-worker]`, `[vs_ingest_worker]`, `[document_profiler]`.
- Key log messages: `Starting unified Responses worker`, `upload_missing_files_to_vector_store`, `Generating document profile`, `Successfully saved document profile`.
- Configure via env: `LOG_LEVEL`, `LOG_JSON`, `DEBUG_VERBOSE_LOG_TEXTS`.

## Local dev & tests
- Python 3.11+. Create a venv and `pip install -r requirements.txt`.
- Run API: `uvicorn app.main:app --reload`.
- Workers: `python run_gdrive_sync_responses.py` to run the full ingestion loop.
- Tests: `pytest tests/`.

## Required environment
- Supabase: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`.
- OpenAI: `OPENAI_API_KEY`.
- CORS: `ALLOWED_ORIGINS` (comma-separated); API prefix is `settings.API_PREFIX` (default `/api`).
- Google Workspace / Drive:
  - `GOOGLE_CREDENTIALS_BASE64` (service account JSON, base64-encoded)
  - `GOOGLE_ADMIN_EMAIL` (subject for domain-wide delegation if used)
  - `GOOGLE_DRIVE_FOLDER_ID` (Shared Drive folder to sync)
- Workspace / Vector Store:
  - `GDRIVE_WORKSPACE_ID` (workspace that owns the Vector Store)
  - `GDRIVE_VECTOR_STORE_ID` (optional explicit VS id; otherwise resolved from `workspace_vector_stores`)
- Throughput tuning:
  - `VS_UPLOAD_BATCH_LIMIT` (default 25) and `VS_UPLOAD_DELAY_MS` (per-file sleep, default 1000).
  - `VS_INGEST_MAX_RETRIES` (default 5) for the dead-letter queue.

## Environment status (as of 2025-11-07)
- `.env` is populated and git-ignored; values mirror Railway.
- All required keys (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `OPENAI_API_KEY`, etc.) are managed in Railway's environment variable settings.
- `PGVECTOR_CONN_STR` is used for direct DB connections if needed.
- `ALLOWED_ORIGINS` includes the Vercel URL for the frontend and localhost for development.
- Do not check secrets into version control; rely on env files and platform envs (Railway/Vercel) for deployment.

## Contract expectations (examples)
- Responses ingestion: `POST /responses/vector-store/ingest/upload` accepts multipart with `workspace_id` and `files[]`; returns `{ vector_store_id, files: [{ id, name, size }], failed?: [{ name, reason }], status }`.

## Integration touchpoints
- Frontend (`chatbot-ui`) forwards ingestion requests to this service.
- This service is the system of record for the ingestion pipeline (Drive Sync → OCR → Vector Store Attach). Retrieval is handled entirely by the frontend.
- All shared data resides in Supabase.

## Ingestion Pipeline (Drive-only, Vector Store Attach Worker)
- Responsibility: This service owns ingestion from a Google Shared Drive folder into Supabase, performs OCR if needed, generates document profiles, and attaches files to an OpenAI Vector Store for retrieval.
- Core flows (implemented):
  - Drive sync: `run_gdrive_sync_responses.py` orchestrates the entire pipeline.
    - Detects new files in the Shared Drive folder (`GOOGLE_DRIVE_FOLDER_ID`).
    - Uploads bytes to Supabase Storage and creates a row in `files`.
    - Upserts a per-workspace join in `file_workspaces` with `ingested=false`.
    - For PDFs, triggers OCR if needed (sets `files.ocr_needed=true`).
    - Deletions: Mirrors deletions from Drive to Supabase and the Vector Store.
  - VS ingest worker: `app/api/Responses/vs_ingest_worker.py::upload_missing_files_to_vector_store()`
      - **Eligibility**: `file_workspaces.ingested=false`, `deleted=false`, `ingest_failed=false`, and text is available (OCR complete or not needed).
      - **Content**: Prefers uploading OCR text (`files.ocr_text_path`); otherwise uploads the original file.
      - **Profiling**: Generates summary/keywords/entities from text content and writes them to `file_workspaces` columns (`profile_summary`, etc.).
      - **Resilience**:
        1) **Baseline Update**: Sets `ingested=true`, `openai_file_id`, `vs_file_id`.
        2) **Metadata Enrichment**: Populates `has_ocr`, `file_ext`, `doc_type`, `meeting_year`, `meeting_month`.
        3) **Profile Flag**: Sets `doc_profile_processed=true` and `doc_profile_processed_at`.
      - **Dead-letter queue**: If a file fails ingestion `VS_INGEST_MAX_RETRIES` times, the worker increments `ingest_retries` and finally sets `ingest_failed=true` to prevent it from clogging the pipeline.
- Vector Store resolution: Uses `GDRIVE_VECTOR_STORE_ID` if set; otherwise looks up `workspace_vector_stores.vector_store_id` by `GDRIVE_WORKSPACE_ID`.

### Metadata backfill utility (existing rows)
- Purpose: Safely populate missing `file_workspaces` metadata for legacy rows.
- Location: `scripts/backfill_file_workspaces_metadata.py`
- Behavior: Scans for NULL columns and derives metadata from filenames. Supports dry runs.

### Deletion mirroring (Drive → Vector Store → DB)
- On missing files in Drive, the sync performs:
  1) Supabase Storage remove and `files` row delete.
  2) `file_workspaces` cleanup.
  3) Best‑effort detach from the Vector Store and delete of the underlying OpenAI file.

### Schema checklist – run in Supabase
- Ensure the following columns exist on `file_workspaces` for the worker to record baseline, enriched, and profile metadata:
  - **Baseline**: `ingested boolean`, `deleted boolean`, `openai_file_id text`, `vs_file_id text`
  - **Ingestion State (Dead-Letter Queue)**: `ingest_retries smallint`, `ingest_failed boolean`
  - **Enriched Metadata**: `has_ocr boolean`, `file_ext text`, `doc_type text`, `meeting_year int`, `meeting_month int`
  - **Profile Metadata**: `profile_summary text`, `profile_keywords text[]`, `profile_entities jsonb`
  - **Processed Flags**: `doc_profile_processed boolean`, `doc_profile_processed_at timestamptz`
- Ensure OCR/storage hints on `files`: `ocr_needed boolean`, `ocr_scanned boolean`, `ocr_text_path text`, `file_path text`, `type text`.
- The `document_profiles` table is **DEPRECATED** and should be removed.

### Research integration (context for frontend)
- The frontend's research agent (`/api/research`) derives soft filters (year, month, doc_type, etc.) from the metadata in the `file_workspaces` table. It may also accept a user-provided date range (`start_date`, `end_date`) and clamp those to the available `meeting_year`/`meeting_month` facets to bias retrieval.
- This backend's primary role is to reliably populate those `file_workspaces` columns so the researcher can guide queries effectively.

## Endpoints map (current)
- Ingestion:
  - `POST /responses/vector-store/ingest/upload` (multipart) – Forwards to the full pipeline.
  - `POST /responses/vector-store/ingest` – Triggers a background ingestion run.
- Google Drive sync:
  - `POST /responses/gdrive/sync` – Triggers a background Drive sync.
- Vector Store maintenance:
  - `GET /responses/list` – List files in the workspace Vector Store.
  - `DELETE /responses/file/{file_id}` – Detach and delete an OpenAI File.
  - `POST /responses/vector-store/purge` – Detach all files.

## Frontend integration note (chatbot-ui)
- The frontend’s `/api/vector-stores/ingest` route forwards to this service’s `POST /responses/vector-store/ingest/upload`. If uploads fail in production, verify Vercel `BACKEND_SEARCH_URL` and backend `ALLOWED_ORIGINS`.

## External schema reference (frontend)
- The frontend (chatbot-ui) repository includes a full Supabase schema snapshot for Copilot/code navigation:
  - Path (in that repo): `supabase/migrations/20251107_manual_schema.sql`
  - Purpose: documentation & AI assistance only; not a runnable migration.

## Maintenance rule
- Whenever this document is updated to reflect new resources or code behavior, remove or update any contradicting information to keep the instructions internally consistent. Treat `../COPILOT-WORKSPACE.md` as the cross-repo source of truth for flags, routes, and flows.
