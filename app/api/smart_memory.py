from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
import logging

router = APIRouter()

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # Temporary hardcoded user ID

class SmartMemoryQuery(BaseModel):
    session_id: str
    topic_name: str
    prompt: str

@router.post("/smart_memory")
async def smart_memory(query: SmartMemoryQuery):
    try:
        query_embedding = embed_text(query.prompt)

        # 🔍 Step 1: Check daily_chat_log for recent related messages
        daily_logs = (
            supabase.table("daily_chat_log")
            .select("*")
            .eq("session_id", query.session_id)
            .eq("user_id", USER_ID)  # ✅ Filter by user
            .order("timestamp", desc=False)
            .execute()
            .data
        )

        if daily_logs:
            recent_context = "\n".join([entry["role"] + ": " + entry["content"] for entry in daily_logs[-10:]])
            return {
                "source": "daily_chat_log",
                "messages": daily_logs,
                "context": recent_context
            }

        # 🧠 Step 2: Search memory table (semantic match)
        search_response = supabase.rpc("match_memory_by_embedding", {
            "query_embedding": query_embedding,
            "match_threshold": 0.7,
            "match_count": 5
        }).execute()

        matches = search_response.data or []
        # ✅ Filter results by user_id (just in case the RPC doesn't)
        matches = [m for m in matches if m.get("user_id") == USER_ID]

        if matches:
            return {
                "source": "memory",
                "messages": matches
            }

        # 📂 Step 3: Fallback
        return {
            "source": "none",
            "messages": [],
            "note": "No memory match found in daily log or long-term memory."
        }

    except Exception as e:
        logging.exception("❌ smart_memory failed")
        raise HTTPException(status_code=500, detail=str(e))
