
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from app.core.supabase_client import supabase

router = APIRouter()

@router.get("/recall_memory")
async def recall_memory(
    session_id: Optional[str] = None,
    topic_name: Optional[str] = None,
    limit: Optional[int] = Query(20, ge=1),
    offset: Optional[int] = Query(0, ge=0)
):
    try:
        base_query = supabase.table("memory").select("*")

        if topic_name:
            base_query = base_query.eq("topic_name", topic_name)
        elif session_id:
            base_query = base_query.eq("session_id", session_id)
        else:
            raise HTTPException(status_code=400, detail="Either topic_name or session_id must be provided.")

        # Get all matching records for count (without limit)
        count_result = base_query.execute()
        total_count = len(count_result.data)

        # Now apply pagination
        paginated_query = base_query.limit(limit).range(offset, offset + limit - 1).order("timestamp", desc=False)
        result = paginated_query.execute()

        return {
            "total_count": total_count,
            "returned": len(result.data),
            "messages": result.data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
