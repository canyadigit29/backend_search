import logging
import time
import uuid
from datetime import datetime

import numpy as np
from openai import OpenAI
from app.core.supabase_client import supabase

logging.basicConfig(level=logging.INFO)
client = OpenAI()

EMBEDDING_MODEL = "text-embedding-3-large"  # 3072-dim
EMBEDDING_DIM = 3072

def normalize_vector(v):
    norm = np.linalg.norm(v)
    return (v / norm).tolist() if norm > 0 else v

def embed_text(text: str) -> list[float]:
    if not isinstance(text, str):
        raise ValueError(f"Expected string input to embed_text(), got {type(text)}")

    if not text.strip():
        raise ValueError("Cannot embed empty text")

    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    embedding = normalize_vector(np.array(response.data[0].embedding))

    if not isinstance(embedding, list) or len(embedding) != EMBEDDING_DIM:
        raise ValueError(f"Embedding shape mismatch: expected {EMBEDDING_DIM}-dim vector, got {len(embedding)}")

    return embedding

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
                logging.warning(
                    f"Embedding failed (attempt {attempt + 1}), retrying... {e}"
                )
                time.sleep(delay * (2**attempt))  # Exponential backoff
            else:
                logging.error(f"Embedding failed after {retries} attempts: {e}")
                raise

def embed_and_store_chunk(chunk):
    chunk_text = chunk["content"]
    if not chunk_text.strip():
        logging.warning(f"⚠️ Skipping empty chunk for file {chunk.get('file_id')}")
        return

    try:
        embedding = retry_embed_text(chunk_text)
        norm = np.linalg.norm(embedding)
        if norm < 0.1 or np.allclose(embedding, 0):
            logging.warning(f"⚠️ Skipping low-quality embedding (norm={norm:.4f}) for chunk of file {chunk.get('file_id')}")
            return

        # Define the structured columns that have dedicated fields in the DB
        structured_keys = [
            "id", "file_id", "user_id", "content", "openai_embedding", "tokens",
            "file_name", "description", "document_type", "meeting_year", 
            "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title"
        ]

        # Prepare data for insert
        data_to_insert = {k: v for k, v in chunk.items() if k in structured_keys}
        
        # All other keys are collected into the 'metadata' JSONB field
        misc_metadata = {k: v for k, v in chunk.items() if k not in structured_keys}
        if misc_metadata:
            data_to_insert["metadata"] = misc_metadata
        
        data_to_insert["openai_embedding"] = embedding

        # Ensure required fields are present
        if "id" not in data_to_insert:
            data_to_insert["id"] = str(uuid.uuid4())
        if "created_at" not in data_to_insert:
            data_to_insert["created_at"] = datetime.utcnow().isoformat()

        print(f"[DEBUG] Data to be inserted: {data_to_insert}")
        # Manually check for an error and raise an exception on failure
        result = supabase.table("file_items").insert(data_to_insert).execute()
        if hasattr(result, 'error') and result.error:
            raise Exception(f"Supabase insert failed: {result.error.message}")

        logging.info(
            f"✅ Stored chunk for file {data_to_insert.get('file_id')} "
            f"(page: {data_to_insert.get('page_number')})"
        )

    except Exception as e:
        logging.exception(f"Unexpected error during embed/store for chunk of file {chunk.get('file_id')}: {e}")
        # Re-raise the exception to signal failure to the caller
        raise

def embed_chunks(chunks):
    if not chunks:
        logging.warning("⚠️ No chunks to embed.")
        return

    for chunk in chunks:
        embed_and_store_chunk(chunk)

def remove_embeddings_for_file(file_id: str):
    try:
        print(f"🧹 Removing all embeddings for file ID: {file_id}")

        delete_result = (
            supabase.table("file_items")
            .delete()
            .eq("file_id", file_id)
            .execute()
        )
        print(f"🧾 Vector delete response: {delete_result}")
        return {"status": "success", "deleted_count": len(delete_result.data)}

    except Exception as e:
        print(f"❌ Failed to remove embeddings: {e}")
        raise

# ✅ Add alias for compatibility
delete_embedding = remove_embeddings_for_file
