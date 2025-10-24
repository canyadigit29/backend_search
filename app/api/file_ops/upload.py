import os
import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from app.api.file_ops.upload_logic import upload_and_ingest_file

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
):
    try:
        contents = await file.read()
        
        # Use the new reusable function
        result = await upload_and_ingest_file(
            file_content=contents,
            file_name=file.filename,
            content_type=file.content_type
        )

        return {"filePath": result["file_path"]}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        # The reusable function will raise HTTPException, but we keep a general catch-all here.
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
