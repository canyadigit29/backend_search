```python
import os
import logging
from datetime import datetime
import fitz  # PyMuPDF
from pdf2image import convert_from_path
import pytesseract
from app.core.supabase_client import supabase
from app.core.config import settings

logger = logging.getLogger(__name__)

def ocr_pdf(file_path: str, file_id: str):
    """
    Performs OCR on a PDF file and stores the extracted text.
    """
    logger.info(f"Starting OCR for file_id: {file_id}")
    supabase.table("files").update({
        "ocr_started_at": datetime.utcnow().isoformat()
    }).eq("id", file_id).execute()

    try:
        # Download the file from storage
        response = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)
        
        local_temp_path = f"/tmp/{file_id}.pdf"
        with open(local_temp_path, "wb") as f:
            f.write(response)

        # Use pdf2image and Tesseract for OCR
        images = convert_from_path(local_temp_path)
        ocr_text = ""
        for i, image in enumerate(images):
            ocr_text += f"---PAGE {i+1}---\n" + pytesseract.image_to_string(image) + "\n"

        # Save the OCR'd text to a new file in storage
        ocr_text_path = f"ocr_results/{file_id}.txt"
        supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
            ocr_text_path,
            ocr_text.encode('utf-8'),
            {"content-type": "text/plain;charset=utf-8"}
        )

        # Update the file record
        supabase.table("files").update({
            "ocr_scanned": True,
            "ocr_completed_at": datetime.utcnow().isoformat(),
            "ocr_text_path": ocr_text_path
        }).eq("id", file_id).execute()

        logger.info(f"OCR completed for file_id: {file_id}. Text stored at {ocr_text_path}")
        os.remove(local_temp_path)
        return {"status": "success", "ocr_text_path": ocr_text_path}

    except Exception as e:
        logger.error(f"OCR failed for file_id {file_id}: {e}")
        supabase.table("files").update({
            "ocr_scanned": False,
            "ocr_completed_at": None,
        }).eq("id", file_id).execute()
        if 'local_temp_path' in locals() and os.path.exists(local_temp_path):
            os.remove(local_temp_path)
        raise

```