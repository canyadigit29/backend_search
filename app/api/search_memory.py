
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

router = APIRouter()

class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 5

@router.post("/search_memory")
async def search_memory(req: MemorySearchRequest):
    try:
        query_embedding = embed_text(req.query)

        result = (
            supabase.rpc("match_memory", {
                "query_embedding": query_embedding,
                "match_count": req.top_k
            }).execute()
        )

        return result.data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
