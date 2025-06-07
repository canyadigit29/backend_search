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

    response = client.embeddings.create(model="text-embedding-3-large", input=text)
    embedding = normalize_vector(np.array(response.data[0].embedding))

    if not isinstance(embedding, list) or len(embedding) != 3072:
        raise ValueError(f"Embedding shape mismatch: expected 3072-dim vector, got {len(embedding)}")

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

def embed_and_store_chunk(chunk_text, project_id, file_name, chunk_index, section_header=None, page_number=None):
    if not is_valid_uuid(project_id):
        logging.error(f"Invalid project_id: {project_id}")
        return {"error": "Invalid project_id"}

    if not chunk_text.strip():
        logging.warning(f"⚠️ Skipping empty chunk {chunk_index} for file {file_name}")
        return {"skipped": True}

    try:
        embedding = retry_embed_text(chunk_text)
        timestamp = datetime.utcnow().isoformat()

        data = {
            "id": str(uuid.uuid4()),
            "content": chunk_text,
            "embedding": embedding,
            "openai_embedding": embedding,  # <-- Write to openai_embedding column
            "project_id": project_id,
            "file_name": file_name,
            "chunk_index": chunk_index,
            "timestamp": timestamp,
            "section_header": section_header,
            "page_number": page_number,
        }

        result = supabase.table("document_chunks").insert(data).execute()

        if getattr(result, "error", None):
            logging.error(f"Supabase insert failed: {result.error.message}")
            return {"error": result.error.message}

        logging.info(
            f"✅ Stored chunk {chunk_index} of {file_name} in project {project_id} (section: {section_header}, page: {page_number})"
        )
        return {"success": True}

    except Exception as e:
        logging.exception(f"Unexpected error during embed/store: {e}")
        return {"error": str(e)}

def embed_chunks(chunks, project_id: str, file_name: str):
    if not chunks:
        logging.warning("⚠️ No chunks to embed.")
        return []

    results = []
    for index, chunk_tuple in enumerate(chunks):
        if isinstance(chunk_tuple, tuple):
            chunk_text, meta = chunk_tuple
            section_header = meta.get("section")
            page_number = meta.get("page")
        else:
            chunk_text = chunk_tuple
            section_header = None
            page_number = None
        result = embed_and_store_chunk(chunk_text, project_id, file_name, index, section_header, page_number)
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