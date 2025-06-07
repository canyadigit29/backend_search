import logging
import time
import uuid
from datetime import datetime
import traceback

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

def embed_texts(texts: list[str], model: str = "text-embedding-3-large") -> list[list[float]]:
    if not texts:
        raise ValueError("No texts provided for embedding.")
    response = client.embeddings.create(model=model, input=texts)
    embeddings = [normalize_vector(np.array(item.embedding)) for item in response.data]
    for emb in embeddings:
        if not isinstance(emb, list) or len(emb) != 3072:
            raise ValueError(f"Embedding shape mismatch: expected 3072-dim vector, got {len(emb)}")
    return embeddings

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

def embed_and_store_chunk(chunk_text, project_id, file_name, chunk_index):
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
        }

        result = supabase.table("document_chunks").insert(data).execute()

        if getattr(result, "error", None):
            logging.error(f"Supabase insert failed: {result.error.message}")
            return {"error": result.error.message}

        logging.info(
            f"✅ Stored chunk {chunk_index} of {file_name} in project {project_id}"
        )
        return {"success": True}

    except Exception as e:
        logging.exception(f"Unexpected error during embed/store: {e}")
        return {"error": str(e)}

def embed_and_store_chunks(chunks: list[str], project_id: str, file_name: str, model: str = "text-embedding-3-large", batch_size: int = 16, chunk_hashes: list[str] = None, section_headers: list = None, page_numbers: list = None):
    print(f"[DEBUG] embed_and_store_chunks called with {len(chunks)} chunks, project_id={project_id}, file_name={file_name}")
    if not is_valid_uuid(project_id):
        logging.error(f"Invalid project_id: {project_id}")
        print(f"[ERROR] Invalid project_id: {project_id}")
        return [{"error": "Invalid project_id"}]
    if not chunks:
        logging.warning("⚠️ No chunks to embed.")
        print("[DEBUG] No chunks to embed.")
        return []
    results = []
    timestamp = datetime.utcnow().isoformat()
    for batch_start in range(0, len(chunks), batch_size):
        batch_chunks = chunks[batch_start:batch_start+batch_size]
        batch_hashes = chunk_hashes[batch_start:batch_start+batch_size] if chunk_hashes else [None]*len(batch_chunks)
        batch_headers = section_headers[batch_start:batch_start+batch_size] if section_headers else [None]*len(batch_chunks)
        batch_pages = page_numbers[batch_start:batch_start+batch_size] if page_numbers else [None]*len(batch_chunks)
        try:
            print(f"[DEBUG] Requesting embedding for batch {batch_start} to {batch_start+len(batch_chunks)-1}")
            embeddings = embed_texts(batch_chunks, model=model)
        except Exception as e:
            logging.error(f"Batch embedding failed: {e}")
            print(f"[ERROR] Batch embedding failed: {e}")
            traceback.print_exc()
            results.extend([{"error": str(e)} for _ in batch_chunks])
            continue
        for i, (chunk_text, embedding) in enumerate(zip(batch_chunks, embeddings)):
            chunk_index = batch_start + i
            data = {
                "id": str(uuid.uuid4()),
                "content": chunk_text,
                "embedding": embedding,
                "openai_embedding": embedding,
                "embedding_model": model,
                "embedding_model_version": "2024-06-06",  # update as needed
                "project_id": project_id,
                "file_name": file_name,
                "chunk_index": chunk_index,
                "timestamp": timestamp,
            }
            if batch_hashes[i]:
                data["chunk_hash"] = batch_hashes[i]
            if batch_headers[i]:
                data["section_header"] = batch_headers[i]
            if batch_pages[i]:
                data["page_number"] = batch_pages[i]
            try:
                result = supabase.table("document_chunks").insert(data).execute()
                if getattr(result, "error", None):
                    logging.error(f"Supabase insert failed: {result.error.message}")
                    print(f"[ERROR] Supabase insert failed: {result.error.message}")
                    results.append({"error": result.error.message})
                else:
                    logging.info(f"✅ Stored chunk {chunk_index} of {file_name} in project {project_id}")
                    print(f"[DEBUG] Stored chunk {chunk_index} of {file_name}")
                    results.append({"success": True})
            except Exception as e:
                logging.error(f"[ERROR] Exception during DB insert: {e}")
                print(f"[ERROR] Exception during DB insert: {e}")
                traceback.print_exc()
                results.append({"error": str(e)})
    print(f"[DEBUG] embed_and_store_chunks finished with {len(results)} results")
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

# For backward compatibility, keep embed_chunks as an alias
def embed_chunks(chunks: list[str], project_id: str, file_name: str, chunk_hashes: list[str] = None, section_headers: list = None, page_numbers: list = None):
    return embed_and_store_chunks(chunks, project_id, file_name, chunk_hashes=chunk_hashes, section_headers=section_headers, page_numbers=page_numbers)