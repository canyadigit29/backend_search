import os
from datetime import datetime

from supabase import Client, create_client


def log_message(
    session_id: str,
    role: str,
    content: str,
    topic_name: str = None,
    user_id: str = None,
):
    try:
        # Railway passes env variables directly — use them here
        supabase_url = os.environ["SUPABASE_URL"]
        supabase_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        supabase: Client = create_client(supabase_url, supabase_key)

        log_entry = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "topic_name": topic_name,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if user_id:
            log_entry["user_id"] = user_id  # ✅ Add user ownership

        result = supabase.table("daily_chat_log").insert(log_entry).execute()

        print(
            f"✅ Logged message: {role} | {session_id} | {topic_name} | user: {user_id}"
        )
        return result
    except Exception as e:
        print(f"❌ Log failed: {str(e)}")
        return None
