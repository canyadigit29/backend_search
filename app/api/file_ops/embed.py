import uuid
import time
from datetime import datetime
from app.core.supabase_client import supabase
from openai import OpenAI
import logging

logging.basicConfig(level=logging.INFO)
client = OpenAI()

def embed_text(text: str) -> list[float]:
    if not text.strip():
        raise ValueError("Cannot embed empty text")

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

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

def embed_chunks(chunks: list[str], project_id: str, file_name: str):
    results = []
    for index, chunk in enumerate(chunks):
        result = embed_and_store_chunk(chunk, project_id, file_name, index)
        results.append(result)
    return results

def remove_embeddings_for_file(file_id: str):
    try:
        # Step 1: Look up the file name by file_id
        file_result = supabase.table("files").select("file_name").eq("id", file_id).maybe_single().execute()
        file_data = getattr(file_result, "data", None)
        if not file_data or "file_name" not in file_data:
            raise Exception(f"File not found for ID: {file_id}")

        file_name = file_data["file_name"]
        print(f"🧹 Removing all embeddings for file: {file_name}")

        # Step 2: Delete from document_chunks
        delete_result = supabase.table("document_chunks").delete().eq("file_name", file_name).execute()
        print(f"🧾 Vector delete response: {delete_result}")
        return {"status": "success", "deleted": delete_result.data}

    except Exception as e:
        print(f"❌ Failed to remove embeddings: {e}")
        raise
