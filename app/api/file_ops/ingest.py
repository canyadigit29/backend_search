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
    project_id = file_record["project_id"]
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET")

    response = supabase.storage.from_(bucket).download(file_path)
    if not response:
        logging.error(f"❌ Could not download file from Supabase: {file_path}")
        return

    local_temp_path = "/tmp/tempfile" + Path(file_path).suffix
    with open(local_temp_path, "wb") as f:
        f.write(response)

    try:
        text = extract_text(local_temp_path)
        logging.info(f"📜 Extracted text length: {len(text.strip())} characters from {file_path}")
    except Exception as e:
        logging.error(f"❌ Failed to extract text from {file_path}: {str(e)}")
        return

    max_chunk_size = 1000
    overlap = 150
    chunks = []

    if len(text.strip()) == 0:
        logging.warning(f"⚠️ Skipping empty file: {file_path}")
        return

    for i in range(0, len(text), max_chunk_size - overlap):
        chunk_text = text[i : i + max_chunk_size].strip()
        if not chunk_text:
            continue

        # ✅ Log type and preview of input
        logging.debug(f"🧠 Embedding input type: {type(chunk_text)}, preview: {str(chunk_text)[:50]}")

        # ✅ Validate input and output of embedding
        if not isinstance(chunk_text, str):
            logging.error(f"❌ Invalid chunk_text type: expected str, got {type(chunk_text)}")
            continue

        embedding_response = client.embeddings.create(
            model="text-embedding-3-large",
            input=chunk_text
        )
        embedding = embedding_response.data[0].embedding

        if len(embedding) != 3072:
            logging.error(f"❌ Embedding shape mismatch: expected 3072-dim, got {len(embedding)}")
            continue

        chunk_data = {
            "id": str(uuid.uuid4()),
            "file_id": file_id,
            "content": chunk_text,
            "embedding": embedding,
            "chunk_index": len(chunks),
            "project_id": project_id,
            "file_name": file_name,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        chunks.append(chunk_data)

    if chunks:
        for chunk in chunks:
        chunk.pop("project_id", None)

    supabase.table("document_chunks").insert(chunks).execute()
        logging.info(f"✅ Inserted and embedded {len(chunks)} chunks from {file_path}")
        supabase.table("files").update(
            {"ingested": True, "ingested_at": datetime.utcnow().isoformat()}
        ).eq("id", file_id).execute()
        logging.info(f"✅ Marked file as ingested: {file_id}")
    else:
        logging.warning(f"⚠️ No valid chunks generated for {file_path}; ingestion skipped.")
