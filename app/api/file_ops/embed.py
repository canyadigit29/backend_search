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

async def embed_chunks(chunks, file_name: str, metadata: dict):
    if not chunks:
        logging.warning("⚠️ No chunks to embed.")
        return []

    # The metadata from the GPT is the primary source.
    # The 'meeting_date' needs to be handled specifically if it exists.
    meeting_date = metadata.pop('meeting_date', None)

    # Prepare the records for batch insertion
    records_to_insert = []
    for chunk in chunks:
        chunk_text = chunk.get("content")
        if not chunk_text or not chunk_text.strip():
            logging.warning(f"⚠️ Skipping empty chunk {chunk.get('chunk_index')} for file {file_name}")
            continue

        try:
            embedding = retry_embed_text(chunk_text)
            norm = np.linalg.norm(embedding)
            if norm < 0.1 or np.allclose(embedding, 0):
                logging.warning(f"⚠️ Skipping low-quality embedding (norm={norm:.4f}) for chunk {chunk.get('chunk_index')} of {file_name}")
                continue

            record = {
                "id": chunk["id"],
                "file_id": chunk["file_id"],
                "user_id": chunk["user_id"],
                "file_name": file_name,
                "content": chunk_text,
                "chunk_index": chunk.get("chunk_index"),
                "page_number": chunk.get("metadata", {}).get("page_number"),
                "doc_type": metadata.get("doc_type"),
                "openai_embedding": embedding,
                "meeting_date": meeting_date,  # This can be None
                "metadata": metadata,  # The rest of the metadata
                "timestamp": datetime.utcnow().isoformat(),
            }
            records_to_insert.append(record)

        except Exception as e:
            logging.error(f"❌ Error embedding chunk {chunk.get('chunk_index')} for {file_name}: {e}")
            # Decide if one failure should stop the whole batch. For now, we skip the failed chunk.
            continue

    if not records_to_insert:
        logging.warning(f"⚠️ No valid chunks were embedded for file {file_name}.")
        return []

    # Batch insert the records
    try:
        result = supabase.table("document_chunks").insert(records_to_insert).execute()
        if getattr(result, "error", None):
            logging.error(f"❌ Supabase batch insert failed: {result.error.message}")
            raise Exception(f"Supabase batch insert failed: {result.error.message}")
        
        logging.info(f"✅ Successfully stored {len(records_to_insert)} chunks for file {file_name}.")
        return result.data
    except Exception as e:
        logging.error(f"❌ An unexpected error occurred during batch insert for {file_name}: {e}")
        raise

def embed_and_store_chunk(chunk):
    # This function is now deprecated in favor of the batch-oriented embed_chunks.
    # It can be removed once all call sites are updated.
    logging.warning("DEPRECATED: embed_and_store_chunk is called. Switch to embed_chunks for batch processing.")
    pass

def remove_embeddings_for_file(file_id: str):
    try:
        file_result = (
            supabase.table("files")
            .select("file_name")
            .eq("id", file_id)
            .maybe_single()
            .execute()
        )
        file_data = getattr(file_result, "data", None)
        if not file_data or "file_name" not in file_data:
            raise Exception(f"File not found for ID: {file_id}")

        # To be safe, we should delete by file_id, not file_name
        print(f"🧹 Removing all embeddings for file_id: {file_id}")

        delete_result = (
            supabase.table("document_chunks")
            .delete()
            .eq("file_id", file_id)
            .execute()
        )
        print(f"🧾 Vector delete response: {delete_result}")
        return {"status": "success", "deleted": delete_result.data}

    except Exception as e:
        print(f"❌ Failed to remove embeddings: {e}")
        raise


# ✅ Add alias for compatibility
delete_embedding = remove_embeddings_for_file
