
from fastapi import APIRouter, HTTPException, Query
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

router = APIRouter()

@router.post("/search")
async def semantic_search(query: str, page: int = Query(1, ge=1)):
    try:
        query_embedding = embed_text(query)

        match_count_per_page = 5
        offset = (page - 1) * match_count_per_page

        search_response = supabase.rpc("match_documents_paged", {
            "query_embedding": query_embedding,
            "match_threshold": 0.75,
            "match_count": match_count_per_page,
            "match_offset": offset
        }).execute()

        if search_response.status_code != 200:
            raise Exception(f"Search RPC failed: {search_response}")

        matches = search_response.data or []

        total_matches = None
        more_available = False
        if page == 1:
            count_response = supabase.rpc("count_matching_documents", {
                "query_embedding": query_embedding,
                "match_threshold": 0.75
            }).execute()

            if count_response.status_code != 200:
                raise Exception(f"Count RPC failed: {count_response}")

            total_matches = count_response.data.get("count", 0)
            more_available = total_matches > match_count_per_page

        return {
            "query": query,
            "matches": matches,
            "total_matches": total_matches,
            "more_available": more_available
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
