from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

router = APIRouter()

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"  # Temporary hardcoded user ID

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
            "match_threshold": 0.6,
            "match_count": match_count_per_page,
            "match_offset": offset
        }).execute()

        matches = search_response.data or []
        enriched_results = []

        for match in matches:
            chunk_id = match.get("id")
            chunk_data = (
                supabase.table("chunks")
                .select("*")
                .eq("id", chunk_id)
                .eq("user_id", USER_ID)  # ✅ ensure ownership
                .single()
                .execute()
                .data
            )

            if not chunk_data:
                continue  # skip if chunk doesn't belong to user

            file_id = chunk_data.get("file_id")
            file_data = (
                supabase.table("files")
                .select("*")
                .eq("id", file_id)
                .eq("user_id", USER_ID)  # ✅ ensure ownership
                .single()
                .execute()
                .data
            )

            if not file_data:
                continue  # skip if file doesn't belong to user

            enriched_result = {
                "chunk_id": chunk_id,
                "chunk_index": chunk_data.get("chunk_index"),
                "content": chunk_data.get("content"),
                "file_id": file_id,
                "filename": file_data.get("file_name"),
                "project_id": file_data.get("project_id")
            }

            if file_data.get("project_id"):
                project_data = (
                    supabase.table("projects")
                    .select("name")
                    .eq("id", file_data["project_id"])
                    .eq("user_id", USER_ID)  # ✅ ensure ownership
                    .single()
                    .execute()
                    .data
                )
                if project_data:
                    enriched_result["project_name"] = project_data.get("name")

            enriched_results.append(enriched_result)

        total_matches = None
        more_available = False
        if page == 1:
            count_response = supabase.rpc("count_matching_documents", {
                "query_embedding": query_embedding,
                "match_threshold": 0.6
            }).execute()

            count_value = count_response.data if isinstance(count_response.data, int) else count_response.data.get("count", 0)
            total_matches = count_value
            more_available = total_matches > match_count_per_page

        return {
            "query": query,
            "matches": enriched_results,
            "total_matches": total_matches,
            "more_available": more_available
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
