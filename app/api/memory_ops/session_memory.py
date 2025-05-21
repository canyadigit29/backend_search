import logging
import time
import uuid
from datetime import datetime

from app.api.file_ops.embed import embed_text
from app.core.supabase_client import supabase

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
                logging.warning(
                    f"Embedding failed (attempt {attempt + 1}), retrying... {e}"
                )
                time.sleep(delay * (2**attempt))  # Exponential backoff
            else:
                logging.error(f"Embedding failed after {retries} attempts: {e}")
                raise

def save_message(user_id, project_id, content, session_id=None, speaker_role=None, message_index=None):
    if not all(map(is_valid_uuid, [user_id, project_id])):
        logging.error("Invalid UUID in user/project ID")
        return {"error": "Invalid UUID input"}

    try:
        embedding = retry_embed_text(content)
        timestamp = datetime.utcnow().isoformat()

        data = {
            "user_id": user_id,
            "project_id": project_id,
            "content": content,
            "embedding": embedding,
            "timestamp": timestamp,
        }
        if session_id is not None:
            data["session_id"] = session_id
        if speaker_role is not None:
            data["speaker_role"] = speaker_role
        if message_index is not None:
            data["message_index"] = message_index

        result = supabase.table("memory_log").insert(data).execute()

        if getattr(result, "error", None):
            logging.error(f"Supabase insert failed: {result.error.message}")
            return {"error": result.error.message}

        logging.info(f"✅ Saved message to memory_log for user {user_id}")
        return {"success": True}

    except Exception as e:
        logging.exception("Unexpected error during message memory save")
        return {"error": str(e)}

def retrieve_memory(tool_args):
    query = tool_args.get("query")
    user_id = tool_args.get("user_id")
    session_id = tool_args.get("session_id")

    if not query or not user_id:
        return {"error": "Missing query or user_id"}

    try:
        query_embedding = retry_embed_text(query)

        rpc_args = {
            "query_embedding": query_embedding,
            "match_threshold": 0.3,
            "match_count": 500,
            "user_id_filter": user_id,
            "session_id_filter": session_id or None
        }

        response = supabase.rpc("match_memory_log", rpc_args).execute()

        if getattr(response, "error", None):
            logging.error(f"Supabase RPC failed: {response.error.message}")
            return {"error": f"Supabase RPC failed: {response.error.message}"}

        matches = response.data or []

        return {"results": matches}

    except Exception as e:
        logging.exception("Error retrieving memory")
        return {"error": str(e)}
