```python
import asyncio
import logging
from fastapi import APIRouter
from app.core.supabase_client import supabase
from app.api.file_ops.ocr import ocr_pdf

logger = logging.getLogger(__name__)

async def run_ocr_worker_once():
    """
    Scans for files that need OCR and processes them.
    """
    logger.info("Starting OCR worker cycle")
    try:
        # Find files that are PDFs and haven't been scanned, and where ocr_needed is true
        files_to_ocr = supabase.table("files").select("*").eq("ocr_needed", True).eq("ocr_scanned", False).execute()

        if not files_to_ocr.data:
            logger.info("No files found requiring OCR.")
            return

        for file in files_to_ocr.data:
            file_id = file.get("id")
            file_path = file.get("file_path")
            
            if not file_id or not file_path:
                logger.warning(f"Skipping invalid file record: {file}")
                continue

            logger.info(f"Found file to OCR: {file_path}")
            try:
                ocr_pdf(file_path=file_path, file_id=file_id)
            except Exception as e:
                logger.error(f"Error during OCR for file {file_id}: {e}")

    except Exception as e:
        logger.error(f"Error in OCR worker: {e}")

router = APIRouter()

@router.post("/run-ocr-worker")
async def run_ocr_worker_endpoint():
    asyncio.create_task(run_ocr_worker_once())
    return {"status": "OCR worker started"}

```