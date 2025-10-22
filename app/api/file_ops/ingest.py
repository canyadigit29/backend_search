import os
import time
import uuid
import asyncio
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

async def process_and_embed_file(file_path: str, file_id: str, user_id: str, metadata: dict):
    logging.info(f"⚙️ Processing file with metadata: {file_path}")
    
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET")
    if not bucket:
        raise ValueError("SUPABASE_STORAGE_BUCKET environment variable not set")

    try:
        response = supabase.storage.from_(bucket).download(file_path)
        if not response:
            raise FileNotFoundError(f"Could not download file from Supabase: {file_path}")
    except Exception as e:
        logging.error(f"❌ Storage download error for {file_path}: {e}")
        raise

    # Use a temporary file to handle different file types for text extraction
    local_temp_path = f"/tmp/{uuid.uuid4()}" + Path(file_path).suffix
    with open(local_temp_path, "wb") as f:
        f.write(response)

    try:
        text = extract_text(local_temp_path)
        logging.info(f"📜 Extracted text length: {len(text.strip())} characters from {file_path}")
        if len(text.strip()) < 50:
            logging.warning(f"⚠️ Extracted text is very short for {file_path}, continuing but may indicate an issue.")
    except Exception as e:
        logging.error(f"❌ Failed to extract text from {file_path}: {e}")
        # Clean up temp file
        os.remove(local_temp_path)
        raise
    finally:
        if os.path.exists(local_temp_path):
            os.remove(local_temp_path)

    if not text.strip():
        logging.warning(f"⚠️ Skipping empty file (no text extracted): {file_path}")
        return

    # --- Pass metadata to the chunking process ---
    chunks = chunk_file(file_id=file_id, user_id=user_id, file_text=text, metadata=metadata)
    if not chunks:
        logging.warning(f"⚠️ No chunks were generated for {file_path}; ingestion skipped.")
        return

    # --- Embedding and storing chunks now also gets the metadata ---
    file_name = Path(file_path).name
    embed_results = await embed_chunks(chunks=chunks, file_name=file_name, metadata=metadata)
    logging.info(f"✅ Embedded and stored {len(embed_results)} chunks from {file_path}")

# Keep the old function for now to avoid breaking existing flows, but it should be deprecated.
def process_file(file_path: str, file_id: str, user_id: str = None):
    logging.warning("DEPRECATED: process_file called. Please switch to the new ingestion flow with process_and_embed_file.")
    # This function can be removed once all callers are updated.
    # For now, it will call the new function with empty metadata.
    asyncio.run(process_and_embed_file(file_path, file_id, user_id, {}))

