from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

router = APIRouter()

class SearchRequest(BaseModel):
    query: str
    page: int = 1

@router.post("/search")
async def semantic_search(request: SearchRequest):
    try:
        query = request.query
        page = request.page
        query_embedding = embed_text(query)

        match_count_per_page = 5
        offset = (page - 1) * match_count_per_page

        search_response = supabase.rpc("match_documents_paged", {
            "query_embedding": query_embedding,
            "match_threshold": 0.75,
            "match_count": match_count_per_page,
            "match_offset": offset
        }).execute()

        matches = search_response.data or []

        total_matches = None
        more_available = False
        if page == 1:
            count_response = supabase.rpc("count_matching_documents", {
                "query_embedding": query_embedding,
                "match_threshold": 0.75
            }).execute()

            count_value = count_response.data if isinstance(count_response.data, int) else count_response.data.get("count", 0)
            total_matches = count_value
            more_available = total_matches > match_count_per_page

        return {
            "query": query,
            "matches": matches,
            "total_matches": total_matches,
            "more_available": more_available
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
