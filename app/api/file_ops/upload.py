import os
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from app.core.supabase_client import supabase
from app.api.file_ops.background_tasks import queue_ingestion_task
import json

router = APIRouter()
logger = logging.getLogger(__name__)

SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")
if not SUPABASE_STORAGE_BUCKET:
    raise RuntimeError("SUPABASE_STORAGE_BUCKET environment variable is not set")

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    name: str = Form(...),
    file_id: str = Form(None)  # Accept it but ignore it
):
    try:
        # 🔍 Lookup file_id and file_path based on name and user_id
        result = supabase.table("files").select("id", "file_path").eq("name", name).eq("user_id", user_id).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="No matching file entry found in files table")

        file_id = result.data["id"]
        file_path = result.data["file_path"]

        contents = await file.read()
        supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            file_path, contents, {"content-type": file.content_type}
        )

        return {"filePath": file_path}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")


@router.post("/upload_with_metadata")
async def upload_with_metadata(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    metadata_json: str = Form(...)
):
    """
    Accepts a file and a JSON string of metadata.
    1. Creates a file record in Supabase.
    2. Uploads the file to storage.
    3. Queues a background task for ingestion, passing along the metadata.
    """
    try:
        # Generate a unique file_id and construct the file_path
        file_id = str(uuid.uuid4())
        file_extension = os.path.splitext(file.filename)[1]
        file_path = f"{user_id}/{file_id}{file_extension}"

        # Parse the metadata JSON string
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in metadata_json field")

        # Create a record in the 'files' table
        file_record = {
            "id": file_id,
            "user_id": user_id,
            "name": file.filename,
            "file_path": file_path,
            "file_type": file.content_type,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        insert_res = supabase.table("files").insert(file_record).execute()
        if getattr(insert_res, 'error', None):
            raise HTTPException(status_code=500, detail=f"Failed to create file record: {insert_res.error.message}")

        # Upload the file to Supabase Storage
        contents = await file.read()
        upload_res = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            file_path, contents, {"content-type": file.content_type}
        )
        if getattr(upload_res, 'error', None):
            # Attempt to clean up the file record if upload fails
            supabase.table("files").delete().eq("id", file_id).execute()
            raise HTTPException(status_code=500, detail=f"Failed to upload file to storage: {upload_res.error.message}")

        # Queue the background ingestion task with the metadata
        queue_ingestion_task(file_id, file_path, user_id, metadata)

        return {"file_id": file_id, "file_path": file_path, "message": "File upload successful, ingestion queued."}

    except HTTPException as he:
        logger.error(f"Upload with metadata failed: {he.detail}")
        raise he
    except Exception as e:
        logger.error(f"An unexpected error occurred during upload_with_metadata: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")
