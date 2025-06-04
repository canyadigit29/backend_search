import json
import os
import logging
from collections import defaultdict

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

def perform_search(tool_args):
    query_embedding = tool_args.get("embedding")
    expected_phrase = tool_args.get("expected_phrase")

    # Optional filters
    file_name_filter = tool_args.get("file_name_filter")
    collection_filter = tool_args.get("collection_filter")
    description_filter = tool_args.get("description_filter")
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")
    user_id_filter = tool_args.get("user_id_filter")

    logger.debug(
        f"🔍 Searching with filters: file_name={file_name_filter}, collection={collection_filter}, date={start_date}–{end_date}, user_id={user_id_filter}"
    )
    logger.debug(f"🔑 Embedding: {query_embedding[:5]}..." if query_embedding else "No embedding")

    if not query_embedding:
        logger.error("❌ No embedding provided in tool_args.")
        return {"error": "Embedding must be provided to perform similarity search."}

    if not user_id_filter:
        logger.error("❌ No user_id provided in tool_args.")
        return {"error": "user_id must be provided to perform search."}

    try:
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": user_id_filter,
            "file_name_filter": file_name_filter,
            "collection_filter": collection_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date,
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

        # Remove grouping by file, just return all matches
        # grouped = defaultdict(list)
        # for match in matches:
        #     file_id = match.get("file_id")
        #     if file_id:
        #         grouped[file_id].append(match)
        #
        # top_file_id = matches[0].get("file_id") if matches else None
        # if top_file_id and top_file_id in grouped:
        #     matches = grouped[top_file_id]
        # Now just return all matches as-is

        if expected_phrase:
            expected_lower = expected_phrase.lower()
            matches = [x for x in matches if expected_lower not in x["content"].lower()]
            logger.debug(f"🔍 {len(matches)} results after omitting phrase: '{expected_phrase}'")

        return {"retrieved_chunks": matches}

    except Exception as e:
        logger.error(f"❌ Error during search: {str(e)}")
        return {"error": f"Error during search: {str(e)}"}


async def semantic_search(request, payload):
    return perform_search(payload)


router = APIRouter()


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
    """
    Endpoint that receives an embedding vector and required user_id, plus optional filters,
    performs a semantic search, and returns chunks formatted for the frontend.
    """
    data = await request.json()

    query = data.get("query") or data.get("user_prompt")
    user_id = data.get("user_id")

    if not query:
        return JSONResponse({"error": "Missing query"}, status_code=400)
    if not user_id:
        return JSONResponse({"error": "Missing user_id"}, status_code=400)

    try:
        embedding = embed_text(query)
    except Exception as e:
        logger.error(f"❌ Failed to generate embedding: {e}")
        return JSONResponse({"error": f"Failed to generate embedding: {e}"}, status_code=500)

    tool_args = {
        "embedding": embedding,
        "user_id_filter": user_id,
        "file_name_filter": data.get("file_name_filter"),
        "collection_filter": data.get("collection_filter"),
        "description_filter": data.get("description_filter"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
    }

    result = perform_search(tool_args)
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=500)

    # matches are the raw file_items + joined file metadata
    matches = result.get("retrieved_chunks", [])

    # Compose output to include all required fields for frontend compatibility
    retrieved_chunks = []
    for m in matches:
        retrieved_chunks.append({
            # file_items table fields
            "id": m.get("id"),
            "file_id": m.get("file_id"),
            "user_id": m.get("user_id"),
            "created_at": m.get("created_at"),
            "updated_at": m.get("updated_at"),
            "sharing": m.get("sharing"),
            "content": m.get("content"),
            "tokens": m.get("tokens"),
            "openai_embedding": m.get("openai_embedding"),
            # search score
            "score": m.get("score"),
            # files table fields (as file_metadata)
            "file_metadata": {
                "file_id": m.get("file_id"),
                "folder_id": m.get("folder_id"),
                "created_at": m.get("file_created_at") or m.get("created_at"),
                "updated_at": m.get("file_updated_at") or m.get("updated_at"),
                "sharing": m.get("file_sharing"),
                "description": m.get("description"),
                "file_path": m.get("file_path"),
                "name": m.get("name") or m.get("file_name"),
                "size": m.get("size"),
                "tokens": m.get("file_tokens"),
                "type": m.get("type"),
                "project_id": m.get("project_id"),
                "message_index": m.get("message_index"),
                "timestamp": m.get("timestamp"),
                "topic_id": m.get("topic_id"),
                "chunk_index": m.get("chunk_index"),
                "embedding_json": m.get("embedding_json"),
                "session_id": m.get("session_id"),
                "status": m.get("status"),
                "content": m.get("file_content"),
                "topic_name": m.get("topic_name"),
                "speaker_role": m.get("speaker_role"),
                "ingested": m.get("ingested"),
                "ingested_at": m.get("ingested_at"),
                "uploaded_at": m.get("uploaded_at"),
                "relevant_date": m.get("relevant_date"),
            }
        })

    return JSONResponse({"retrieved_chunks": retrieved_chunks})


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
