#!/bin/bash

# Start the Google Drive sync worker in the background
echo "Starting Google Drive sync worker..."
python run_gdrive_sync.py &

# Start the ingestion worker in the background
echo "Starting ingestion worker..."
python run_worker.py &

# Start the Uvicorn server in the foreground
echo "Starting Uvicorn server..."
uvicorn llama_server:app --host 0.0.0.0 --port 8000
