from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
from app.core.log_writer import log_message  # ✅ added import
import uuid

router = APIRouter()

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # Temporary hardcoded user ID

class MemoryEntry(BaseModel):
    session_id: str
    message_index: int
    role: str  # 'user' or 'assistant'
    content: str
    topic_id: str = None
    topic_name: str = "General"

@router.post("/store_memory")
async def store_memory(entry: MemoryEntry):
    topic_id = entry.topic_id or str(uuid.uuid4())
    embedding = embed_text(entry.content)

    # ✅ Log to daily_chat_log with user_id
    log_message(
        session_id=entry.session_id,
        role=entry.role,
        content=entry.content,
        topic_name=entry.topic_name,
        user_id=USER_ID  # ✅ Inject user ownership
    )

    try:
        # ✅ Store in memory table with user_id
        supabase.table("memory").insert({
            "session_id": entry.session_id,
            "message_index": entry.message_index,
            "role": entry.role,
            "content": entry.content,
            "timestamp": datetime.utcnow().isoformat(),
            "embedding": embedding,
            "topic_id": topic_id,
            "topic_name": entry.topic_name,
            "user_id": USER_ID  # ✅ Inject user ownership
        }).execute()

        return {"status": "success", "topic_id": topic_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Memory store failed: {str(e)}")
