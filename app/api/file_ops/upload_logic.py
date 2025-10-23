import os
import uuid
import logging
from datetime import datetime
from fastapi import HTTPException, UploadFile
from app.core.supabase_client import supabase
from app.core.config import settings

logger = logging.getLogger(__name__)

async def upload_and_ingest_file(
    file_content: bytes,
    file_name: str,
    content_type: str,
    user_id: str = "gdrive_sync",  # Default user for sync operations
):
    """
    Handles the logic of creating a file record in the database and uploading the file to storage.
    This is a reusable function for both direct uploads and syncs.
    """
    try:
        file_extension = os.path.splitext(file_name)[1]
        file_path = f"{uuid.uuid4()}{file_extension}"

        # Step 1: Insert metadata into the 'files' table
        inserted_file = supabase.table("files").insert({
            "user_id": user_id,
            "name": file_name,
            "file_path": file_path,
            "file_type": content_type,
            "created_at": datetime.utcnow().isoformat(),
            "ingested": False, # Set to False to be picked up by the worker
        }).execute()

        if not inserted_file.data:
            raise HTTPException(status_code=500, detail="Failed to create file record in database.")

        file_id = inserted_file.data[0]['id']
        logger.info(f"Created file record with ID: {file_id} for path: {file_path}")

        # Step 2: Upload the actual file to Supabase Storage
        supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
            file_path, file_content, {"content-type": content_type}
        )
        logger.info(f"Successfully uploaded file to storage at: {file_path}")

        # The background worker will now pick this file up for ingestion.
        return {"file_id": file_id, "file_path": file_path}

    except Exception as e:
        logger.error(f"Error in upload_and_ingest_file: {e}")
        # In a real app, you might want to add cleanup logic here,
        # e.g., delete the DB row if storage upload fails.
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during file upload: {e}")

