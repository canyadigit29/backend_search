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
      - Writes updates in stages for resilience:
        1) Baseline (always attempted): `ingested=true`, `openai_file_id`, and `vs_file_id`.
        2) Metadata enrichment (best-effort; tolerates missing columns): `has_ocr`, `file_ext`, `doc_type`, `meeting_year`, `meeting_month`.
        3) Document profile flag (best-effort): `doc_profile_processed=true`, `doc_profile_processed_at=now()`.
- Vector Store resolution: Uses `GDRIVE_VECTOR_STORE_ID` if set; otherwise looks up `workspace_vector_stores.vector_store_id` by `GDRIVE_WORKSPACE_ID`.
- Retrieval remains UI-side via Responses API + File Search; this service does not handle chat, only ingestion and optional hybrid RAG.

### Planned: Multi-folder Drive sync → multiple Vector Stores per workspace
- Objective: Support mapping multiple Google Drive subfolders to distinct Vector Stores (e.g., Agendas, Minutes, Transcripts) while retaining a default store for uncategorized files.

Backend changes (high-level)
1) Folder→store mapping
  - Add a mapping source (DB table preferred) linking `(workspace_id, label, vector_store_id, drive_folder_id?)`.
  - Resolve target store for a file based on the Drive subfolder it originates from; fall back to the default store mapping.

2) GDrive sync updates
  - Allow configuration of multiple subfolders per workspace.
  - Populate/refresh `file_workspaces` with `ingested=false` and set the resolved `target_store_label/id` as a hint for the VS worker.
  - Continue OCR detection; set `files.ocr_needed` and enqueue as today.

3) VS ingest worker updates
  - On eligibility, prefer uploading OCR text (`ocr_text_path`) when available, else original bytes.
  - Attach to the resolved `vector_store_id` (from the folder mapping or default).
  - Use file_batches for bulk attach; poll batch status and errors. Retry failed items.
  - Record both `openai_file_id` and `vs_file_id` in `file_workspaces` for detaching/deleting.
  - If `files.retrieve` returns 404 for a referenced file, auto-detach to avoid perpetual `in_progress`.

4) HTTP-first OpenAI ops
  - Use REST endpoints for list/delete with Authorization only (no `OpenAI-Beta: assistants=v2` header).
  - Add short timeouts and small exponential backoff on 429/5xx.

5) Health & monitoring
  - Provide a small admin endpoint/command to summarize per-store counts, recent errors, and dangling attachments.

Acceptance criteria
- Drive sync recognizes multiple subfolders per workspace and sets `target_store` hints.
- VS worker attaches to the correct store, records IDs, and reports batch errors.
- No long-lived `in_progress` items (auto-detach when underlying file is missing).
- REST operations use Authorization only (no `assistants=v2` header) and include retries/timeouts.

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
  - If schema columns are missing, the worker logs a best‑effort warning and still writes baseline fields; add columns per SQL below.
- Absence of OpenAI upload/attach logs in the backend indicates the VS worker isn’t running or files aren’t yet eligible (e.g., waiting for OCR to finish).

### Operations: how to run workers
- Drive sync: run `run_gdrive_sync.py` or `run_gdrive_sync_responses.py` (or mount inside `app/workers/main_worker.py` if consolidated) to execute `run_responses_gdrive_sync()` on a schedule.
- VS ingest: schedule periodic calls to `upload_missing_files_to_vector_store()`; batch/pace with the envs above. You can also trigger via `POST /responses/vector-store/ingest` (background task).

### Metadata backfill utility (existing rows)
- Purpose: Safely populate missing `file_workspaces` metadata for legacy rows, including `has_ocr`, `file_ext`, `doc_type`, `meeting_year`, and `meeting_month`.
- Location: `scripts/backfill_file_workspaces_metadata.py`
- Behavior:
  - Scans only rows where any of the target columns is NULL for the specified workspace.
  - Derives year/doc_type and month from the filename; sets `has_ocr` from `files.ocr_scanned`/`files.ocr_text_path`; sets `file_ext` from the filename.
  - Paginates (default 500), logs a summary, and supports a dry run.
- Usage (envs/CLI):
  - `BACKFILL_WORKSPACE_ID` or pass `workspace_id` as the first CLI arg; falls back to `GDRIVE_WORKSPACE_ID`.
  - Optional: `DRY_RUN=1` to preview without writes; `PAGE_SIZE=500` to tune pagination.
  - Example (PowerShell): `set BACKFILL_WORKSPACE_ID=<workspace_id>; set DRY_RUN=1; python scripts/backfill_file_workspaces_metadata.py`

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

### Schema checklist (document profiles) – run in Supabase
- Ensure the following columns exist on `file_workspaces` so the worker can record both baseline and enriched metadata:
  - Baseline: `ingested boolean not null default false`, `deleted boolean not null default false`, `openai_file_id text`, `vs_file_id text`
  - Metadata: `has_ocr boolean`, `file_ext text`, `doc_type text`, `meeting_year int`, `meeting_month int`
  - Processed flags: `doc_profile_processed boolean not null default false`, `doc_profile_processed_at timestamptz`
- Also ensure OCR/storage hints on `files`: `ocr_needed boolean`, `ocr_scanned boolean`, `ocr_text_path text`, `file_path text`, `type text`
- See the workspace UI repo instructions for a combined SQL that also creates the `research_reports` table used by the frontend.

### Research integration (context for frontend)
- The frontend saves research runs to `research_reports` and lists them in a sidebar panel.
- The research route (`/api/research` in the UI) now derives soft filters (year, month, doc_type, meeting_body, ordinance_number) from `file_workspaces` metadata plus the user question and injects them as hints to bias File Search retrieval. Backend ingestion should continue to enrich these facet columns so the researcher can guide queries effectively.
- Multi‑store routing (planned) will allow the researcher to attach multiple Vector Stores when the question spans categories; today a single workspace store is attached.

### Health & reconciliation (optional but recommended)
- Health endpoint: add a tiny route (e.g., `GET /responses/vector-store/health`) that summarizes per‑workspace counts: pending vs ingested, rows missing `doc_profile_processed`, and recent worker errors.
- Reconciliation job (if UI direct uploads are used): periodically list current Vector Store attachments and upsert/update `file_workspaces` rows (set `ingested/openai_file_id/vs_file_id` and fill basic metadata); this complements the Drive‑only ingestion path.

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
  - `POST /responses/vector-store/hard-purge` – aggressive looped detach with optional file deletes; polls until empty or max iterations.
  - `GET /responses/vector-store/health` – summarize DB join vs Vector Store attachments and flag dangling items.

## Frontend integration note (chatbot-ui)
- The frontend’s `/api/vector-stores/ingest` route forwards to this service’s `POST /responses/vector-store/ingest/upload`. If uploads fail in production, verify Vercel `BACKEND_SEARCH_URL` and CORS `ALLOWED_ORIGINS`, and confirm the endpoint path matches.

## External schema reference (frontend)
- The frontend (chatbot-ui) repository includes a full Supabase schema snapshot for Copilot/code navigation:
  - Path (in that repo): `supabase/migrations/20251107_manual_schema.sql`
  - Purpose: documentation & AI assistance only; not a runnable migration. Backend code may rely on tables/functions documented there.
  - When backend schema changes require frontend awareness, regenerate the snapshot in the UI repo rather than copying it here to avoid divergence.

  ## Maintenance rule
  - Whenever this document is updated to reflect new resources or code behavior, remove or update any contradicting information to keep the instructions internally consistent. Treat `../COPILOT-WORKSPACE.md` and the frontend’s `COPILOT-WORKSPACE.md` as cross-repo sources of truth for flags, routes, and flows.
