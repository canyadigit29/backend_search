#!/bin/bash

# Source Railway's environment variables file if it exists
if [ -f "$RAILWAY_ENVIRONMENT_FILE" ]; then
  echo "Sourcing Railway environment variables..."
  source "$RAILWAY_ENVIRONMENT_FILE"
fi

# Set the Python path to include the current directory, fixing module import errors
export PYTHONPATH=.

# Give the container a few seconds to initialize networking and other services
echo "Initializing..."
sleep 5

# Resolve virtualenv created by uv
VENV_DIR="/app/.venv"
VENV_PY="$VENV_DIR/bin/python"
VENV_UVICORN="$VENV_DIR/bin/uvicorn"

# Start the background worker loop(s).
# Unified loop: GDrive sync -> OCR sweep -> Vector Store ingestion, on a single cadence.
if [ "${ENABLE_RESPONSES_GDRIVE_SYNC}" = "true" ] || [ "${ENABLE_RESPONSES_GDRIVE_SYNC}" = "1" ]; then
  echo "Starting unified Responses worker (sync + OCR + VS ingest)..."
  if [ -x "$VENV_PY" ]; then
    "$VENV_PY" run_gdrive_sync_responses.py | awk '{ print "[responses-worker] " $0 }' &
  else
    python run_gdrive_sync_responses.py | awk '{ print "[responses-worker] " $0 }' &
  fi
else
  echo "Responses worker disabled (ENABLE_RESPONSES_GDRIVE_SYNC is not set)."
fi

# Start the Uvicorn server in the foreground. This must be the last command.
PORT_TO_USE="${PORT:-8000}"
echo "Starting Uvicorn server on port ${PORT_TO_USE}..."
if [ -x "$VENV_UVICORN" ]; then
  "$VENV_UVICORN" app.main:app --host 0.0.0.0 --port "${PORT_TO_USE}"
else
  uvicorn app.main:app --host 0.0.0.0 --port "${PORT_TO_USE}"
fi
