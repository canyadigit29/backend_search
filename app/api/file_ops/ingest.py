from fastapi import APIRouter
from app.core.supabase_client import supabase
from app.api.file_ops.chunk import chunk_file
from app.api.file_ops.embed import embed_chunks
from app.api.file_ops.embed import remove_embeddings_for_file as delete_embedding
from datetime import datetime
import time

router = APIRouter()

@router.post("/process")
def api_process_file(file_path: str, file_id: str, user_id: str = None):
    process_file(file_path, file_id, user_id)
    return {"status": "processing started"}

def process_file(file_path: str, file_id: str, user_id: str = None):
    print(f"⚙️ Processing file: {file_path} (ID: {file_id}, User: {user_id})")
    max_retries = 24
    retry_interval = 5.0
    file_record = None

    for attempt in range(max_retries):
        result = supabase.table("files").select("*").eq("id", file_id).execute()
        if result and result.data:
            file_record = result.data[0]
            break
        print(f"⏳ Waiting for file to appear in DB... attempt {attempt + 1}")
        time.sleep(retry_interval)

    if not file_record:
        raise Exception(f"File record not found after {max_retries} retries: {file_path}")

    # Get required fields for embedding
    file_name = file_record["file_name"]
    project_id = file_record["project_id"]

    # Chunk the file (this will return the actual chunk text list)
    chunks = chunk_file(file_id, user_id=user_id)

    # Embed the chunks and store in document_chunks table
    embed_chunks(chunks, project_id, file_name)

    # Mark the file as ingested
    supabase.table("files").update({
        "ingested": True,
        "ingested_at": datetime.utcnow().isoformat()
    }).eq("id", file_id).execute()
