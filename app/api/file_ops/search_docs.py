
import json
import os
import logging
from collections import defaultdict

from app.core.supabase_client import create_client

logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"

def perform_search(tool_args):
    query_embedding = tool_args.get("embedding")
    expected_phrase = tool_args.get("expected_phrase")

    # Optional filters
    file_name_filter = tool_args.get("file_name_filter")
    collection_filter = tool_args.get("collection_filter")
    description_filter = tool_args.get("description_filter")
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")

    logger.debug(f"🔍 Searching with filters: file_name={file_name_filter}, collection={collection_filter}, date={start_date}–{end_date}")
    logger.debug(f"🔑 Embedding: {query_embedding[:5]}..." if query_embedding else "No embedding")

    if not query_embedding:
        logger.error("❌ No embedding provided in tool_args.")
        return {"error": "Embedding must be provided to perform similarity search."}

    try:
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": USER_ID,
            "file_name_filter": file_name_filter,
            "collection_filter": collection_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date
        }

        response = supabase.rpc("match_documents", rpc_args).execute()

        if getattr(response, "error", None):
            logger.error(f"❌ Supabase RPC failed: {response.error.message}")
            return {"error": f"Supabase RPC failed: {response.error.message}"}

        matches = response.data or []
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        if matches:
            top = matches[0]
            preview = top["content"][:200].replace("\n", " ")
            logger.debug(f"🔝 Top match (score {top.get('score')}): {preview}")

        grouped = defaultdict(list)
        for match in matches:
            file_id = match.get("file_id")
            if file_id:
                grouped[file_id].append(match)

        top_file_id = matches[0].get("file_id") if matches else None
        if top_file_id and top_file_id in grouped:
            matches = grouped[top_file_id]

        if expected_phrase:
            expected_lower = expected_phrase.lower()
            matches = [x for x in matches if expected_lower not in x["content"].lower()]
            logger.debug(f"🔍 {len(matches)} results after omitting phrase: '{expected_phrase}'")

        return {"results": matches}

    except Exception as e:
        logger.error(f"❌ Error during search: {str(e)}")
        return {"error": f"Error during search: {str(e)}"}

async def semantic_search(request, payload):
    return perform_search(payload)


from fastapi import APIRouter, Request

router = APIRouter()

@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
