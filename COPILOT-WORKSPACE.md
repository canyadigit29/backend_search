# Copilot Workspace Integration Guide

This guide helps Copilot models (and humans) understand how the frontend (chatbot-ui) and backend (backend_search) work together across feature flags, routes, and shared data.

## Flags and shared config

- Frontend flags
  - USE_OPENAI_FILE_SEARCH (server, in Next runtime): turns on Responses + Vector Store routes in chatbot-ui.
  - NEXT_PUBLIC_USE_OPENAI_FILE_SEARCH (client): switches chat send and enables the sidebar "Vector Store Files" panel.
  - NEXT_PUBLIC_USE_BACKEND_INGEST (client): sidebar upload uses backend ingestion forwarder instead of UI direct upload.
- Backend env of interest
  - BACKEND_SEARCH_URL: base URL for backend_search when the UI forwards to it.
  - ALLOWED_ORIGINS: must include the Vercel domain and localhost for CORS on backend_search.
- Shared data key
  - Supabase table `workspace_vector_stores(workspace_id, vector_store_id)` is the single source of truth for which OpenAI Vector Store belongs to a workspace.

## End-to-end flows

### 1) Upload files to the Vector Store

- UI direct upload (default)
  - Client → UI route: `POST /api/vector-stores/upload` (multipart: `workspace_id`, `files[]`)
  - UI server: creates OpenAI File(s) with `purpose:"assistants"`, attaches to the workspace Vector Store.
  - Listing source: UI lists via `POST /api/vector-stores/list` (OpenAI is the source-of-truth).

- Backend ingestion (toggle via NEXT_PUBLIC_USE_BACKEND_INGEST=true)
  - Client → UI forwarder: `POST /api/vector-stores/ingest`
  - UI → Backend: forwards to `POST {BACKEND_SEARCH_URL}/responses/vector-store/ingest/upload`
  - Backend: optional OCR/text enrichment, OpenAI upload + attach, upserts `files` and `file_workspaces` (marks `ingested=true`, records IDs).

### 2) List / Delete Vector Store files

- List (UI): `POST /api/vector-stores/list` → returns `{ vector_store_id, files: [{ id, name?, size?, status? }] }`
- Delete (UI): `POST /api/vector-stores/delete` → detaches file from Vector Store; can also delete underlying OpenAI file.
- Maintenance (backend optional):
  - List: `GET /responses/list`
  - Delete: `DELETE /responses/file/{file_id}`
  - Soft-delete per-workspace: `POST /responses/file/soft-delete`
  - Purge workspace store: `POST /responses/vector-store/purge`

### 3) Chat with File Search

- Client (flag ON) → UI: `POST /api/chat/respond`
  - Server assembles transcript from Supabase when `chat_id` is provided, augments `instructions`, and calls OpenAI Responses with File Search attached:
  - `responses.create/stream({ model: "gpt-5", tools: [{ type: "file_search", vector_store_ids: [vector_store_id] }], tool_choice: "auto" })`
  - Streams normalized plaintext back to the client.

## Minimal env checklist (prod)

- Frontend (Vercel)
  - `OPENAI_API_KEY`
  - `NEXT_PUBLIC_SUPABASE_URL`
  - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  - `SUPABASE_SERVICE_ROLE_KEY`
  - `USE_OPENAI_FILE_SEARCH=true`
  - `NEXT_PUBLIC_USE_OPENAI_FILE_SEARCH=true`
  - Optional: `NEXT_PUBLIC_USE_BACKEND_INGEST=true`; `BACKEND_SEARCH_URL`

- Backend (Railway/other)
  - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE`, `SUPABASE_STORAGE_BUCKET`
  - `OPENAI_API_KEY`
  - `PGVECTOR_CONN_STR` (if used)
  - `ALLOWED_ORIGINS` includes the Vercel domain and localhost
  - Drive ingestion (optional): Google credentials + `GDRIVE_WORKSPACE_ID` (+ optional `GDRIVE_VECTOR_STORE_ID`)

## Troubleshooting pointers

- 404 from UI routes when feature flag off: ensure `USE_OPENAI_FILE_SEARCH=true` on the server.
- Uploads via backend ingest failing: verify `BACKEND_SEARCH_URL` and backend `ALLOWED_ORIGINS`.
- Missing Vector Store: create mapping via `POST /api/vector-stores/create` (or the admin script) to persist `workspace_vector_stores.vector_store_id`.
