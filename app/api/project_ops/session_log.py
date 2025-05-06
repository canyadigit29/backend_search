from fastapi import APIRouter, HTTPException  # ✅ Added missing import
from app.core.supabase_client import supabase
import uuid
import datetime

router = APIRouter()

@router.post("/log_session")
async def log_session(user_query: str, matched_documents: list):
    try:
        log_entry = {
            "id": str(uuid.uuid4()),
            "query": user_query,
            "matched_documents": matched_documents,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }
        response = supabase.table("session_logs").insert(log_entry).execute()

        if response.get("error"):
            raise Exception(response["error"]["message"])

        return {"message": "Session logged successfully."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
