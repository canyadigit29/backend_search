import logging
from fastapi import APIRouter, File, UploadFile, HTTPException
from app.services.file_processing_service import FileProcessingService

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
):
    try:
        contents = await file.read()
        
        result = await FileProcessingService.upload_and_register_file(
            file_content=contents,
            file_name=file.filename,
            content_type=file.content_type
        )

        return {"filePath": result["file_path"]}

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during file upload: {e}")
