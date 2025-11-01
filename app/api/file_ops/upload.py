import logging
from typing import Optional
from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from app.services.file_processing_service import FileProcessingService

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: Optional[str] = Form(None),
):
    try:
        contents = await file.read()
        
        # FileProcessingService currently overrides user_id internally,
        # but its signature requires it. Provide a placeholder if missing.
        result = await FileProcessingService.upload_and_register_file(
            user_id=user_id or "anonymous",
            file_content=contents,
            file_name=file.filename,
            content_type=file.content_type
        )

        return {"filePath": result["file_path"]}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during file upload: {e}")
