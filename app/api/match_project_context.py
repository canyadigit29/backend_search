from fastapi import APIRouter, HTTPException, Query
from app.core.supabase_client import supabase
import logging

router = APIRouter()
logger = logging.getLogger("project_match")
logger.setLevel(logging.DEBUG)

@router.get("/match_project_context")
async def match_project_context(
    q: str = Query(..., min_length=2),
    user_id: str = Query(...)
):
    try:
        logger.debug(f"🔍 Checking if prompt references a known project: {q}")
        logger.debug(f"🔑 Using user_id: {user_id}")

        # Pull all projects for the provided user
        projects = (
            supabase.table("projects")
            .select("id, name, description")
            .eq("user_id", user_id)
            .execute()
        ).data or []

        # Try to match based on name or description
        matched_project = next(
            (p for p in projects if (
                p["name"].lower() in q.lower() or
                (p.get("description") and p["description"].lower() in q.lower())
            )),
            None
        )

        if matched_project:
            logger.debug(f"✅ Matched project: {matched_project['name']}")
        else:
            logger.info("📭 No project match found.")

        return {"matched_project": matched_project}

    except Exception as e:
        logger.exception("❌ Error during project match lookup.")
        raise HTTPException(status_code=500, detail=str(e))
