
import uuid
import time
from datetime import datetime
from app.core.supabase_client import supabase
from app.utils.embed_text import embed_text  # Assumes your embed_text() hits OpenAI
import logging

logging.basicConfig(level=logging.INFO)

def is_valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False

def retry_embed_text(text, retries=3, delay=1.5):
    for attempt in range(retries):
        try:
            return embed_text(text)
        except Exception as e:
            if attempt < retries - 1:
                logging.warning(f"Embedding failed (attempt {attempt + 1}), retrying... {e}")
                time.sleep(delay * (2 ** attempt))  # Exponential backoff
            else:
                logging.error(f"Embedding failed after {retries} attempts: {e}")
                raise

def embed_and_store_chunk(chunk_text, project_id, file_name, chunk_index):
    if not is_valid_uuid(project_id):
        logging.error(f"Invalid project_id: {project_id}")
        return {"error": "Invalid project_id"}

    try:
        embedding = retry_embed_text(chunk_text)
        timestamp = datetime.utcnow().isoformat()

        data = {
            "content": chunk_text,
            "embedding": embedding,
            "project_id": project_id,
            "file_name": file_name,
            "chunk_index": chunk_index,
            "timestamp": timestamp,
        }

        result = supabase.table("document_chunks").insert(data).execute()

        if result.error:
            logging.error(f"Supabase insert failed: {result.error.message}")
            return {"error": result.error.message}

        logging.info(f"✅ Stored chunk {chunk_index} of {file_name} in project {project_id}")
        return {"success": True}

    except Exception as e:
        logging.exception(f"Unexpected error during embed/store: {e}")
        return {"error": str(e)}
