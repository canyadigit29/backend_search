
import uuid
import time
from datetime import datetime
from app.core.supabase_client import supabase
from app.utils.embed_text import embed_text  # Must call OpenAI's embedding API
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

def save_message(user_id, session_id, project_id, content):
    if not all(map(is_valid_uuid, [user_id, session_id, project_id])):
        logging.error("Invalid UUID in user/session/project ID")
        return {"error": "Invalid UUID input"}

    try:
        embedding = retry_embed_text(content)
        timestamp = datetime.utcnow().isoformat()

        data = {
            "user_id": user_id,
            "session_id": session_id,
            "project_id": project_id,
            "content": content,
            "embedding": embedding,
            "timestamp": timestamp,
        }

        result = supabase.table("memory_log").insert(data).execute()

        if result.error:
            logging.error(f"Supabase insert failed: {result.error.message}")
            return {"error": result.error.message}

        logging.info(f"✅ Saved message to memory_log for user {user_id}, session {session_id}")
        return {"success": True}

    except Exception as e:
        logging.exception("Unexpected error during message memory save")
        return {"error": str(e)}
