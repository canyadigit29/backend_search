import os
import time
import uuid
from datetime import datetime
from pathlib import Path
import logging
import traceback

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
    print(f"[DEBUG] process_file called with file_path={file_path}, file_id={file_id}, user_id={user_id}")
    max_retries = 24
    retry_interval = 5.0
    file_record = None
    for attempt in range(max_retries):
        try:
            result = supabase.table("files").select("*").eq("id", file_id).execute()
            print(f"[DEBUG] DB lookup attempt {attempt+1}: found={bool(result and result.data)}")
            if result and result.data:
                file_record = result.data[0]
                break
        except Exception as e:
            logging.error(f"[ERROR] Exception during DB lookup: {e}")
            print(f"[ERROR] Exception during DB lookup: {e}")
            traceback.print_exc()
        logging.info(f"⏳ Waiting for file to appear in DB... attempt {attempt + 1}")
        time.sleep(retry_interval)
    if not file_record:
        print(f"[ERROR] File record not found after {max_retries} retries: {file_path}")
        raise Exception(f"File record not found after {max_retries} retries: {file_path}")
    file_name = file_record["file_name"]
    project_id = file_record["project_id"]
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET")
    try:
        response = supabase.storage.from_(bucket).download(file_path)
        print(f"[DEBUG] Downloaded file from storage: {file_path}")
    except Exception as e:
        logging.error(f"[ERROR] Could not download file from Supabase: {file_path} - {e}")
        print(f"[ERROR] Could not download file from Supabase: {file_path} - {e}")
        traceback.print_exc()
        return
    if not response:
        logging.error(f"❌ Could not download file from Supabase: {file_path}")
        print(f"[ERROR] No response downloading file: {file_path}")
        return
    local_temp_path = "/tmp/tempfile" + Path(file_path).suffix
    try:
        with open(local_temp_path, "wb") as f:
            f.write(response)
        print(f"[DEBUG] Wrote file to local temp path: {local_temp_path}")
    except Exception as e:
        logging.error(f"[ERROR] Failed to write file to local temp: {e}")
        print(f"[ERROR] Failed to write file to local temp: {e}")
        traceback.print_exc()
        return
    try:
        text = extract_text(local_temp_path)
        logging.info(f"📜 Extracted text length: {len(text.strip())} characters from {file_path}")
        print(f"[DEBUG] Extracted text from {local_temp_path}, length={len(text.strip())}")
    except Exception as e:
        logging.error(f"❌ Failed to extract text from {file_path}: {str(e)}")
        print(f"[ERROR] Failed to extract text: {e}")
        traceback.print_exc()
        return
    max_chunk_size = 1000
    overlap = 150
    chunks = []
    if len(text.strip()) == 0:
        logging.warning(f"⚠️ Skipping empty file: {file_path}")
        print(f"[DEBUG] Skipping empty file: {file_path}")
        return
    for i in range(0, len(text), max_chunk_size - overlap):
        chunk_text = text[i : i + max_chunk_size].strip()
        if not chunk_text:
            continue
        logging.debug(f"🧠 Embedding input type: {type(chunk_text)}, preview: {str(chunk_text)[:50]}")
        print(f"[DEBUG] Embedding chunk {i//(max_chunk_size-overlap)}: {chunk_text[:50]}...")
        if not isinstance(chunk_text, str):
            logging.error(f"❌ Invalid chunk_text type: expected str, got {type(chunk_text)}")
            print(f"[ERROR] Invalid chunk_text type: {type(chunk_text)}")
            continue
        try:
            embedding_response = client.embeddings.create(
                model="text-embedding-3-large",
                input=chunk_text
            )
            embedding = embedding_response.data[0].embedding
            print(f"[DEBUG] Got embedding for chunk {i//(max_chunk_size-overlap)}")
        except Exception as e:
            logging.error(f"[ERROR] Embedding failed: {e}")
            print(f"[ERROR] Embedding failed: {e}")
            traceback.print_exc()
            continue
        if len(embedding) != 3072:
            logging.error(f"❌ Embedding shape mismatch: expected 3072-dim, got {len(embedding)}")
            print(f"[ERROR] Embedding shape mismatch: got {len(embedding)}")
            continue
        chunk_data = {
            "id": str(uuid.uuid4()),
            "file_id": file_id,
            "content": chunk_text,
            "embedding": embedding,
            "openai_embedding": embedding,
            "chunk_index": len(chunks),
            "project_id": project_id,
            "file_name": file_name,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        chunks.append(chunk_data)
        print(f"[DEBUG] Stored chunk_data for chunk {len(chunks)-1}")
    print(f"[DEBUG] Finished process_file for {file_path}, total chunks: {len(chunks)}")
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