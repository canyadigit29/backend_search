# Local development helpers

This folder groups convenience scripts and local-only assets so the repo root stays clean. Nothing here is required for library usage; they are helpers for running workers and dev loops on your machine.

Contents
- `run_worker.py` – Runs the unified ingestion/profile worker loop (hourly).
- `run_gdrive_sync.py` – Legacy Drive sync loop (non-Responses path).
- `run_gdrive_sync_responses.py` – Drive sync + OCR sweep + Vector Store ingest loop.
- `precache_model.py` – (Legacy) cross-encoder warm cache; safe to remove if unused.
- `start.sh` – Container entrypoint that can launch the unified worker plus the API server.

Environment
- Put your local env file at `local_dev/.env` (git-ignored). Copy from `local_dev/.env.example`.
- Google credentials, if using a file, can live at `local_dev/google-credentials.json` (git-ignored). Prefer the `GOOGLE_CREDENTIALS_BASE64` env instead.

Run
```powershell
# Windows PowerShell
Set-Location ..  # project root
python .\local_dev\run_worker.py
# or
python .\local_dev\run_gdrive_sync_responses.py
```

These scripts rely on imports from `app/**`, so run them from the project root where `PYTHONPATH` naturally includes `.`.
