from fastapi import APIRouter, HTTPException
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

router = APIRouter()

@router.post("/search")
async def semantic_search(query: str):
    try:
        query_embedding = embed_text(query)

        # Perform semantic search
        search_response = supabase.rpc("match_documents", {
            "query_embedding": query_embedding,
            "match_threshold": 0.75,
            "match_count": 5
        }).execute()

        if search_response.get("error"):
            raise Exception(search_response["error"]["message"])

        matches = search_response["data"] if search_response else []

        return {
            "query": query,
            "matches": matches
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
