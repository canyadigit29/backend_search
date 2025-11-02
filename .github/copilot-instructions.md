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
- Core services in `app/core/**`: `config.py` (env), `openai_client.py`, `supabase_client.py`, `logger.py`, `logging_config.py`.
- Background tasks: `app/workers/**` with `MainWorker` orchestrated from `/api/run-worker`.

## Retrieval details
Retrieval is now performed by the frontend via the OpenAI Responses API + File Search. This backend no longer exposes hybrid search endpoints.

## Summary generation
- If `SEARCH_ASSISTANT_ID` is set, the service uses the OpenAI Assistants API to return JSON with `summary_markdown`, `used_source_labels`, `follow_up_questions`.
- If assistant output is missing or incomplete, the API responds with `fallback_text` and still returns ordered `sources`.
 - Considering migration to the OpenAI Responses API + File Search? Port persona to `instructions`, attach your `vector_store_id`, and keep the rerank/hydration stages. See in the sibling `openai-cookbook` folder:
   - `Responses_API_Complete_Cheat_Sheet (1).md` (Complete Responses API interaction cheat sheet)
   - `openai-responses-mini-cookbook.md` (Responses API)
   - `file_search_reference.md` (File Search specifics)
   - `openai-vector-stores-guide.md` (Vector Stores end-to-end + ops)
   - `OpenAI_Vector_Store_Complete_Interaction_Cheat_Sheet.md` (complete endpoint map)
   - `OpenAI_Vector_Store_Endpoints_Cheat_Sheet.pdf` (endpoint quick lookup)

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

## Environment status (as of 2025-11-02)
- `.env` is populated and git-ignored; values mirror Railway. Notable keys:
  - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE`, `SUPABASE_STORAGE_BUCKET` are set.
  - `OPENAI_API_KEY` is present; `SEARCH_ASSISTANT_ID` optional.
  - `PGVECTOR_CONN_STR` points to the remote instance.
  - `ALLOWED_ORIGINS` includes the Vercel URL and localhost.
- Do not check secrets into version control; rely on env files and platform envs (Railway/Vercel) for deployment.

## Contract expectations (examples)
- Responses ingestion: `POST /responses/vector-store/ingest/upload` accepts multipart with `workspace_id` and `files[]`; returns `{ vector_store_id, files: [{ id, name, size }], failed?: [{ name, reason }], status }`.

## Integration touchpoints
- Frontend (`chatbot-ui`) may call this service for hybrid RAG. Keep this repo focused on retrieval/summary; don’t duplicate UI logic.
- Database-side logic lives in Supabase functions: `match_file_items_openai` (semantic) and `match_file_items_fts` (keyword); keep those performant and return only metadata, not full content.
 - If the UI’s File Search feature flag is ON, uploads go through `/api/vector-stores/upload` (UI direct) or `/api/vector-stores/ingest` (forwarded here to `/responses/vector-store/ingest/upload`) and chat goes via `/api/chat/respond`; this service is optional for chat, but remains the system of record for ingestion (OCR/Drive sync) and mirroring deletions.

## Post-migration scope (Drive-only ingestion, Vector Store attach worker)
- Responsibility: This service owns ingestion from a Google Shared Drive folder into Supabase, OCR if needed, and attachment to an OpenAI Vector Store for retrieval.
- Core flows (implemented):
  - Drive sync: `app/api/Responses/gdrive_sync.py::run_responses_gdrive_sync()`
    - Detects new files in the Shared Drive folder (`GOOGLE_DRIVE_FOLDER_ID`).
    - Uploads bytes to Supabase Storage and creates a row in `files`.
    - Upserts a per-workspace join in `file_workspaces` with `ingested=false` so the VS worker can act.
    - For PDFs, runs a quick text check and triggers OCR if needed (sets `files.ocr_needed=true` and enqueues OCR).
    - Deletions: If a file disappears from Drive, removes from Supabase Storage and `files`, deletes related `file_workspaces`, and best‑effort detaches/deletes the corresponding OpenAI Vector Store file(s) by filename.
  - VS ingest worker: `app/api/Responses/vs_ingest_worker.py::upload_missing_files_to_vector_store()`
    - Eligibility per workspace: `file_workspaces.ingested=false AND file_workspaces.deleted=false AND (files.ocr_needed=false OR files.ocr_scanned=true)`.
    - Prefers uploading OCR text (`files.ocr_text_path`) when available; otherwise uploads original file bytes.
    - Creates an OpenAI File (`purpose:"assistants"`) and attaches it to the workspace Vector Store.
    - Marks `file_workspaces.ingested=true`, records `openai_file_id` and optional `vs_file_id`.
- Vector Store resolution: Uses `GDRIVE_VECTOR_STORE_ID` if set; otherwise looks up `workspace_vector_stores.vector_store_id` by `GDRIVE_WORKSPACE_ID`.
- Retrieval remains UI-side via Responses API + File Search; this service does not handle chat, only ingestion and optional hybrid RAG.

### Required environment (expanded)
- Google Workspace / Drive:
  - `GOOGLE_CREDENTIALS_BASE64` (service account JSON, base64-encoded)
  - `GOOGLE_ADMIN_EMAIL` (subject for domain-wide delegation if used)
  - `GOOGLE_DRIVE_FOLDER_ID` (Shared Drive folder to sync)
- Workspace / Vector Store:
  - `GDRIVE_WORKSPACE_ID` (workspace that owns the Vector Store)
  - `GDRIVE_VECTOR_STORE_ID` (optional explicit VS id; otherwise resolved from `workspace_vector_stores`)
- Throughput tuning:
  - `VS_UPLOAD_BATCH_LIMIT` (default 1+) and `VS_UPLOAD_DELAY_MS` (per-file sleep) for worker pacing

### Logging & verification
- In Railway logs you should see:
  - Drive sync entries: `[responses.gdrive] New file: …`, OCR decisions, and `Google Drive sync finished` summaries.
  - OCR pipeline logs: `Starting OCR`, `Processing page X/N`, and `Successfully completed OCR`.
  - VS worker logs on errors or warnings during upload/attach, and updates to `file_workspaces`.
- Absence of OpenAI upload/attach logs in the backend indicates the VS worker isn’t running or files aren’t yet eligible (e.g., waiting for OCR to finish).

### Operations: how to run workers
- Drive sync: run `run_gdrive_sync.py` or `run_gdrive_sync_responses.py` (or mount inside `app/workers/main_worker.py` if consolidated) to execute `run_responses_gdrive_sync()` on a schedule.
- VS ingest: schedule periodic calls to `upload_missing_files_to_vector_store()`; batch/pace with the envs above. You can also trigger via `POST /responses/vector-store/ingest` (background task).

### Deletion mirroring (Drive → Vector Store → DB)
- On missing files in Drive, the sync performs:
  1) Supabase Storage remove and `files` row delete (best‑effort cascades via `file_workspaces`).
  2) `file_workspaces` cleanup for the workspace.
  3) Best‑effort detach from the Vector Store by filename mapping, then delete the underlying OpenAI file.

### What’s next
- Make Drive the only upload surface (no UI uploads to OpenAI):
  - UI buttons become “Manage files in Shared Drive” (or optionally Google Picker for add-only) while this backend continues to sync and attach.
- Ensure the VS ingest worker is scheduled in production and has the env to resolve `vector_store_id`.
- Confirm the service account is a member of the Shared Drive with Content Manager (or above) so list/download works across the org.
- Add a small health metric: counts of `new_files_processed`, `ocr_started`, `uploaded`, `errors`, `files_deleted`, `vs_deleted` for observability.

### Agent loop (optional)
- If you add server-side tool orchestration (beyond OCR), use the Responses API tool loop patterns from `openai-cookbook/openai-agent-builder-guide.md`:
  - Declare function tools, execute model tool_calls, return results with `previous_response_id`.
  - Guard with small `MAX_STEPS`; prefer `response_format` for structured outputs.

## Endpoints map (current)
- Ingestion:
  - `POST /responses/vector-store/ingest/upload` (multipart) – OCR-as-needed, metadata derivation, upload to OpenAI Files, attach to Vector Store, and DB upsert of `files` + `file_workspaces`.
  - `POST /responses/vector-store/ingest` – trigger background ingestion of pending Supabase files to Vector Store.
- Google Drive sync:
  - `POST /responses/gdrive/sync` – background sync Drive → Supabase (upload + OCR decision), no embedding.
- Vector Store maintenance:
  - `GET /responses/list` – list files attached to the workspace Vector Store.
  - `DELETE /responses/file/{file_id}` – detach and delete OpenAI File.
  - `POST /responses/file/soft-delete` – per-workspace soft delete and flag reset.
  - `POST /responses/vector-store/purge` – detach all files (optionally reset DB flags).

## Frontend integration note (chatbot-ui)
- The frontend’s `/api/vector-stores/ingest` route forwards to this service’s `POST /responses/vector-store/ingest/upload`. If uploads fail in production, verify Vercel `BACKEND_SEARCH_URL` and CORS `ALLOWED_ORIGINS`, and confirm the endpoint path matches.
