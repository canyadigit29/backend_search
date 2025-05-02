
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
import uuid

router = APIRouter()

class MemoryEntry(BaseModel):
    session_id: str
    message_index: int
    role: str  # 'user' or 'assistant'
    content: str
    topic_id: str = None  # Optional: auto-assigned if not provided
    topic_name: str = "General"  # Default topic name

@router.post("/store_memory")
async def store_memory(entry: MemoryEntry):
    try:
        # Generate topic_id if not provided
        topic_id = entry.topic_id or str(uuid.uuid4())

        # Generate embedding
        embedding = embed_text(entry.content)

        # Insert into Supabase
        result = supabase.table("memory").insert({
            "session_id": entry.session_id,
            "message_index": entry.message_index,
            "role": entry.role,
            "content": entry.content,
            "timestamp": datetime.utcnow().isoformat(),
            "embedding": embedding,
            "topic_id": topic_id,
            "topic_name": entry.topic_name
        }).execute()

        if result.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail="Failed to store memory in Supabase.")

        return {"status": "success", "topic_id": topic_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
