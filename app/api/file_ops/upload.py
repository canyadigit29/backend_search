import os
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from app.core.supabase_client import supabase
from app.api.file_ops.background_tasks import queue_ingestion_task
from app.api.assistant.schemas import UploadMetadataRequest, UploadMetadataResponse
from app.core.openai_client import get_openai_client
import json

router = APIRouter()
logger = logging.getLogger(__name__)

SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")
if not SUPABASE_STORAGE_BUCKET:
    raise RuntimeError("SUPABASE_STORAGE_BUCKET environment variable is not set")

@router.post("/upload_with_metadata", response_model=UploadMetadataResponse)
async def upload_with_metadata(request: UploadMetadataRequest):
    """
    Accepts a file_id and metadata from the GPT environment.
    1. Downloads the file from OpenAI using the file_id.
    2. Creates a file record in the Supabase 'files' table.
    3. Uploads the file content to Supabase Storage.
    4. Queues a background task for ingestion with the provided metadata.
    """
    try:
        user_id = request.user.id
        if not user_id:
            raise HTTPException(status_code=400, detail="Missing 'id' in user object")

        openai_client = get_openai_client()
        
        # 1. Download file from OpenAI
        try:
            file_content_response = openai_client.files.content(request.file_id)
            file_content = file_content_response.read()
            
            # We need the original filename. Let's retrieve the file metadata from OpenAI.
            file_metadata = openai_client.files.retrieve(request.file_id)
            original_filename = file_metadata.filename
            
        except Exception as e:
            logger.error(f"Failed to download file {request.file_id} from OpenAI: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to download file from OpenAI: {e}")

        # 2. Create a file record in Supabase
        file_id = str(uuid.uuid4())
        file_extension = os.path.splitext(original_filename)[1]
        file_path = f"{user_id}/{file_id}{file_extension}"
        
        # Determine content type, default if not obvious
        content_type = 'application/octet-stream' # Default
        if file_extension.lower() == '.pdf':
            content_type = 'application/pdf'
        elif file_extension.lower() in ['.txt', '.md']:
            content_type = 'text/plain'
        elif file_extension.lower() in ['.docx']:
            content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


        file_record = {
            "id": file_id,
            "user_id": user_id,
            "name": original_filename,
            "file_path": file_path,
            "file_type": content_type,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        insert_res = supabase.table("files").insert(file_record).execute()
        if getattr(insert_res, 'error', None):
            logger.error(f"Failed to create file record: {insert_res.error.message}")
            raise HTTPException(status_code=500, detail=f"Failed to create file record: {insert_res.error.message}")

        # 3. Upload the file to Supabase Storage
        try:
            supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
                file_path, file_content, {"content-type": content_type}
            )
        except Exception as e:
            logger.error(f"Failed to upload file to storage: {e}")
            # Attempt to clean up the file record if upload fails
            supabase.table("files").delete().eq("id", file_id).execute()
            raise HTTPException(status_code=500, detail=f"Failed to upload file to storage: {e}")

        # 4. Queue the background ingestion task
        try:
            metadata = json.loads(request.metadata_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in metadata_json field")
            
        queue_ingestion_task(file_id, file_path, user_id, metadata)

        return UploadMetadataResponse(
            message="File processed and ingestion queued.",
            file_id=file_id
        )

    except HTTPException as he:
        logger.error(f"upload_with_metadata failed: {he.detail}")
        raise he
    except Exception as e:
        logger.error(f"An unexpected error occurred in upload_with_metadata: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")
