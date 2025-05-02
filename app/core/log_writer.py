import os
from datetime import datetime
from supabase import create_client, Client

def log_message(session_id: str, role: str, content: str, topic_name: str = None):
    try:
        # Railway passes env variables directly — use them here
        supabase_url = os.environ["SUPABASE_URL"]
        supabase_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        supabase: Client = create_client(supabase_url, supabase_key)

        result = supabase.table("daily_chat_log").insert({
            "session_id": session_id,
            "role": role,
            "content": content,
            "topic_name": topic_name,
            "timestamp": datetime.utcnow().isoformat()
        }).execute()

        print(f"✅ Logged message: {role} | {session_id} | {topic_name}")
        return result
    except Exception as e:
        print(f"❌ Log failed: {str(e)}")
        return None
