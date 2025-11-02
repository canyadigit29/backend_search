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

# Start the background workers.
# Their output is piped to awk to add a prefix, making logs easier to read.
if [ "${ENABLE_RESPONSES_GDRIVE_SYNC}" = "true" ] || [ "${ENABLE_RESPONSES_GDRIVE_SYNC}" = "1" ]; then
  echo "Starting Responses Google Drive sync worker..."
  python run_gdrive_sync_responses.py | awk '{ print "[gdrive-sync-responses] " $0 }' &
else
  echo "Responses GDrive sync worker disabled (ENABLE_RESPONSES_GDRIVE_SYNC is not set)."
fi

echo "Starting ingestion worker (attachments to Vector Store)..."
python run_worker.py | awk '{ print "[ingestion-worker] " $0 }' &

# Start the Uvicorn server in the foreground. This must be the last command.
PORT_TO_USE="${PORT:-8000}"
echo "Starting Uvicorn server on port ${PORT_TO_USE}..."
uvicorn app.main:app --host 0.0.0.0 --port "${PORT_TO_USE}"
