import logging
import os
from datetime import datetime
import pytesseract
from pdf2image import convert_from_path
from app.core.supabase_client import supabase
from app.core.config import settings

logger = logging.getLogger(__name__)

def ocr_pdf(file_path: str, file_id: str):
    """
    Performs OCR on a PDF file, saves the text, and updates the database.
    """
    try:
        logger.info(f"Starting OCR for file_path: {file_path}")
        
        # Update DB to mark OCR as started
        supabase.table("files").update({
            "ocr_started_at": datetime.utcnow().isoformat()
        }).eq("id", file_id).execute()

        # Download the file from Supabase storage
        local_pdf_path = f"/tmp/{file_id}.pdf"
        with open(local_pdf_path, 'wb') as f:
            response = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)
            f.write(response)
        logger.info(f"Downloaded PDF to {local_pdf_path}")

        # Convert PDF to images
        images = convert_from_path(local_pdf_path)
        
        # Perform OCR on each image and concatenate text
        full_text = ""
        for i, image in enumerate(images):
            logger.info(f"Processing page {i+1}/{len(images)}")
            text = pytesseract.image_to_string(image)
            full_text += text + "\n\n"
        
        # Save the extracted text to a file
        ocr_text_filename = f"ocr_text_{file_id}.txt"
        local_text_path = f"/tmp/{ocr_text_filename}"
        with open(local_text_path, "w", encoding="utf-8") as f:
            f.write(full_text)
        logger.info(f"Saved OCR text to {local_text_path}")

        # Upload the text file to Supabase storage
        storage_text_path = f"ocr_results/{ocr_text_filename}"
        with open(local_text_path, 'rb') as f:
            supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
                storage_text_path, f.read(), {"content-type": "text/plain"}
            )
        logger.info(f"Uploaded OCR text to Supabase storage at {storage_text_path}")

        # Update the file record in Supabase
        update_data = {
            "ocr_scanned": True,
            "ocr_completed_at": datetime.utcnow().isoformat(),
            "ocr_text_path": storage_text_path,
            "ingested": False # Mark for ingestion by the other worker
        }
        supabase.table("files").update(update_data).eq("id", file_id).execute()
        
        logger.info(f"Successfully completed OCR for file_id: {file_id}")

    except Exception as e:
        logger.error(f"Error during OCR for file {file_id}: {e}", exc_info=True)
        # Update the database to reflect the error
        try:
            supabase.table("files").update({
                "ocr_scanned": False,
                "ocr_error": str(e)
            }).eq("id", file_id).execute()
        except Exception as db_error:
            logger.error(f"Could not even update the database with OCR error: {db_error}")
        # Re-raise the exception to ensure the worker knows it failed
        raise
    finally:
        # Clean up local files
        if os.path.exists(local_pdf_path):
            os.remove(local_pdf_path)
        if os.path.exists(local_text_path):
            os.remove(local_text_path)
        logger.info("Cleaned up temporary files.")