#!/bin/bash

# Local-dev container-friendly starter. Optional.

export PYTHONPATH=.

if [ -f "local_dev/.env" ]; then
  echo "Loading env from local_dev/.env"
  set -a
  source local_dev/.env
  set +a
fi

if [ "$ENABLE_RESPONSES_GDRIVE_SYNC" = "true" ] || [ "$ENABLE_RESPONSES_GDRIVE_SYNC" = "1" ]; then
  echo "Starting unified Responses worker (sync + OCR + VS ingest)..."
  python local_dev/run_gdrive_sync_responses.py | awk '{ print "[responses-worker] " $0 }' &
else
  echo "Responses worker disabled (ENABLE_RESPONSES_GDRIVE_SYNC not set)."
fi

echo "Starting Uvicorn server on port ${PORT:-8000}..."
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"