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
echo "Starting Google Drive sync worker..."
python run_gdrive_sync.py | awk '{ print "[gdrive-sync] " $0 }' &

echo "Starting ingestion worker..."
python run_worker.py | awk '{ print "[ingestion-worker] " $0 }' &

# Start the Uvicorn server in the foreground. This must be the last command.
echo "Starting Uvicorn server..."
uvicorn app.main:app --host 0.0.0.0 --port 8000
