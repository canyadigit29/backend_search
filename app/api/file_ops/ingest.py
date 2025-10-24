import os
import time
import uuid
from datetime import datetime
from pathlib import Path
import logging

from fastapi import APIRouter
from app.core.supabase_client import supabase
from app.core.extract_text import extract_text
from openai import OpenAI
from app.api.file_ops.chunk import chunk_file
from app.api.file_ops.embed import embed_chunks

router = APIRouter()
logging.basicConfig(level=logging.INFO)
client = OpenAI()

@router.post("/process")
def api_process_file(file_path: str, file_id: str, user_id: str = None):
    process_file(file_path, file_id, user_id)
    return {"status": "processing started"}

def process_file(file_path: str, file_id: str, user_id: str = None):
    logging.info(f"⚙️ Processing file: {file_path} (ID: {file_id}, User: {user_id})")
    max_retries = 24
    retry_interval = 5.0
    file_record = None

    for attempt in range(max_retries):
        result = supabase.table("files").select("*").eq("id", file_id).execute()
        if result and result.data:
            file_record = result.data[0]
            break
        logging.info(f"⏳ Waiting for file to appear in DB... attempt {attempt + 1}")
        time.sleep(retry_interval)

    if not file_record:
        raise Exception(f"File record not found after {max_retries} retries: {file_path}")

    file_name = file_record["file_name"]
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET")

    if file_record.get("ocr_scanned") and file_record.get("ocr_text_path"):
        # If OCR has been performed, use the OCR'd text
        ocr_text_path = file_record["ocr_text_path"]
        response = supabase.storage.from_(bucket).download(ocr_text_path)
        text = response.decode('utf-8')
        logging.info(f"📜 Using OCR'd text from {ocr_text_path}")
    else:
        # Otherwise, extract text directly from the original file
        response = supabase.storage.from_(bucket).download(file_path)
        if not response:
            logging.error(f"❌ Could not download file from Supabase: {file_path}")
            return

        local_temp_path = "/tmp/tempfile" + Path(file_path).suffix
        with open(local_temp_path, "wb") as f:
            f.write(response)

        try:
            text = extract_text(local_temp_path)
            if text is None:
                # If text extraction fails, mark for OCR and skip for now
                logging.warning(f"⚠️ Text extraction failed for {file_path}. Marking for OCR.")
                supabase.table("files").update({"ocr_needed": True}).eq("id", file_id).execute()
                os.remove(local_temp_path)
                return
            logging.info(f"📜 Extracted text length: {len(text.strip())} characters from {file_path}")
            if len(text.strip()) < 100:
                logging.warning(f"⚠️ Extracted text is very short, possible extraction issue for {file_path}")
        except Exception as e:
            logging.error(f"❌ Failed to extract text from {file_path}: {str(e)}")
            os.remove(local_temp_path)
            return
        finally:
            if os.path.exists(local_temp_path):
                os.remove(local_temp_path)

    if len(text.strip()) == 0:
        logging.warning(f"⚠️ Skipping empty file: {file_path}")
        return

    # --- Improved chunking: sentence-aware, overlap, structure-aware ---
    # Use chunk_file to get semantic chunks with metadata
    chunks = chunk_file(file_id, user_id)
    if not chunks or len(chunks) == 0:
        logging.warning(f"⚠️ No valid chunks generated for {file_path}; ingestion skipped.")
        return

    # --- Embedding and storing chunks (with section/page metadata) ---
    embed_results = embed_chunks(chunks, file_name)  # Removed project_id
    logging.info(f"✅ Embedded and stored {len(embed_results)} chunks from {file_path}")

    supabase.table("files").update(
        {"ingested": True, "ingested_at": datetime.utcnow().isoformat()}
    ).eq("id", file_id).execute()
    logging.info(f"✅ Marked file as ingested: {file_id}")
