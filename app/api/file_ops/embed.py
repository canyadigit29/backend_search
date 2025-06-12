import logging
import time
import uuid
from datetime import datetime

import numpy as np
from openai import OpenAI
from app.core.supabase_client import supabase

logging.basicConfig(level=logging.INFO)
client = OpenAI()

def normalize_vector(v):
    norm = np.linalg.norm(v)
    return (v / norm).tolist() if norm > 0 else v

def embed_text(text: str) -> list[float]:
    if not isinstance(text, str):
        raise ValueError(f"Expected string input to embed_text(), got {type(text)}")

    if not text.strip():
        raise ValueError("Cannot embed empty text")

    response = client.embeddings.create(model="text-embedding-3-small", input=text)
    embedding = normalize_vector(np.array(response.data[0].embedding))

    if not isinstance(embedding, list) or len(embedding) != 1536:
        raise ValueError(f"Embedding shape mismatch: expected 1536-dim vector, got {len(embedding)}")

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
        logging.warning(f"⚠️ Skipping empty chunk {chunk.get('chunk_index')} for file {chunk.get('file_name')}")
        return {"skipped": True}

    try:
        embedding = retry_embed_text(chunk_text)
        # Embedding quality checks
        norm = np.linalg.norm(embedding)
        if norm < 0.1 or np.allclose(embedding, 0):
            logging.warning(f"⚠️ Skipping low-quality embedding (norm={norm:.4f}) for chunk {chunk.get('chunk_index')} of {chunk.get('file_name')}")
            return {"skipped": True, "reason": "low-quality embedding", "norm": float(norm)}
        timestamp = datetime.utcnow().isoformat()

        # Prepare data for insert, including all original chunk fields
        data = dict(chunk)  # Copy all fields from chunk
        data["openai_embedding"] = embedding
        # Do NOT write to 'embedding' column anymore
        data["timestamp"] = timestamp
        # Ensure id is present
        if "id" not in data:
            data["id"] = str(uuid.uuid4())

        result = supabase.table("document_chunks").insert(data).execute()
        if getattr(result, "error", None):
            logging.error(f"Supabase insert failed: {result.error.message}")
            return {"error": result.error.message}

        logging.info(
            f"✅ Stored chunk {data.get('chunk_index')} of {data.get('file_name')} (section: {data.get('section_header')}, page: {data.get('page_number')})"
        )
        return {"success": True}

    except Exception as e:
        logging.exception(f"Unexpected error during embed/store: {e}")
        return {"error": str(e)}

def embed_chunks(chunks, file_name: str = None):
    if not chunks:
        logging.warning("⚠️ No chunks to embed.")
        return []

    results = []
    for chunk in chunks:
        # Optionally set file_name if not present
        if file_name and not chunk.get("file_name"):
            chunk["file_name"] = file_name
        result = embed_and_store_chunk(chunk)
        results.append(result)
    return results

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

        file_name = file_data["file_name"]
        print(f"🧹 Removing all embeddings for file: {file_name}")

        delete_result = (
            supabase.table("document_chunks")
            .delete()
            .eq("file_name", file_name)
            .execute()
        )
        print(f"🧾 Vector delete response: {delete_result}")
        return {"status": "success", "deleted": delete_result.data}

    except Exception as e:
        print(f"❌ Failed to remove embeddings: {e}")
        raise

# ✅ Add alias for compatibility
delete_embedding = remove_embeddings_for_file
