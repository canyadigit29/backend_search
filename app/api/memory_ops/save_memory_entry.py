
import uuid
from datetime import datetime
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

async def save_memory_entry(user_id: str, session_id: str, speaker_role: str, content: str):
    try:
        embedding = embed_text(content)
        entry_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()

        response = supabase.table("memory").insert({
            "id": entry_id,
            "session_id": session_id,
            "speaker_role": speaker_role,
            "content": content,
            "timestamp": timestamp,
            "embedding": embedding,
            "user_id": user_id
        }).execute()

        if response.error:
            print(f"❌ Supabase insert error: {response.error.message}")
        else:
            print(f"✅ Memory saved: {entry_id}")

    except Exception as e:
        print(f"❌ save_memory_entry failed: {str(e)}")
