
import os
from supabase import create_client, Client
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

def log_message(session_id: str, role: str, content: str, topic_name: str = None):
    try:
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
