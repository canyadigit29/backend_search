
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
from datetime import datetime
import logging

router = APIRouter()

class SearchRequest(BaseModel):
    query: str
    page: int = 1

@router.post("/search_memory")
async def search_memory(request: SearchRequest):
    try:
        query = request.query
        query_embedding = embed_text(query)

        raw_matches = supabase.rpc("match_memory_by_embedding", {
            "query_embedding": query_embedding,
            "match_threshold": 0.7,
            "match_count": 15
        }).execute().data or []

        def rank(entry):
            try:
                ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                days_old = (datetime.utcnow() - ts).days
                recency_bonus = 0.02 if days_old <= 7 else 0.01 if days_old <= 30 else 0
                return entry.get("similarity", 0) + recency_bonus
            except:
                return entry.get("similarity", 0)

        ranked = sorted(raw_matches, key=rank, reverse=True)
        top = ranked[:5]

        return {
            "query": query,
            "returned": len(top),
            "matches": top
        }

    except Exception as e:
        logging.exception("❌ search_memory failed")
        raise HTTPException(status_code=500, detail=str(e))
