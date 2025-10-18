import json
import os
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.llama_query_transform import llama_query_transform

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

router = APIRouter()


def perform_search(tool_args):
    """
    Perform vector-based search against Supabase.
    Returns raw document chunks (no summarization).
    """
    query_embedding = tool_args.get("embedding")
    search_query = tool_args.get("search_query")
    user_id_filter = tool_args.get("user_id_filter")
    file_name_filter = tool_args.get("file_name_filter")
    description_filter = tool_args.get("description_filter")
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")

    if not query_embedding:
        text_to_embed = search_query or tool_args.get("user_prompt")
        if not text_to_embed:
            return {"error": "Missing text for embedding."}
        query_embedding = embed_text(text_to_embed)

    if not user_id_filter:
        return {"error": "user_id must be provided to perform search."}

    try:
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": user_id_filter,
            "file_name_filter": file_name_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date,
            "match_threshold": tool_args.get("match_threshold", 0.5),
            match_count = min(tool_args.get("match_count", 100), 200)
        }

        response = supabase.rpc("match_documents", rpc_args).execute()
        if getattr(response, "error", None):
            return {"error": f"Supabase RPC failed: {response.error.message}"}

        matches = response.data or []
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {"retrieved_chunks": matches[:100]}
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}


def get_neighbor_chunks(chunk, all_chunks_by_file):
    file_id = chunk.get("file_id")
    chunk_index = chunk.get("chunk_index")
    if file_id is None or chunk_index is None:
        return None, None
    file_chunks = all_chunks_by_file.get(file_id, [])
    prev_chunk = next_chunk = None
    for c in file_chunks:
        if c.get("chunk_index") == chunk_index - 1:
            prev_chunk = c
        if c.get("chunk_index") == chunk_index + 1:
            next_chunk = c
    return prev_chunk, next_chunk


@router.post("/assistant/search_docs")
async def assistant_search_docs(request: Request):
    """
    Simplified assistant endpoint — returns raw excerpts for GPT summarization.
    """
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON payload: {e}"}, status_code=400)

    user_prompt = data.get("query") or data.get("input") or data.get("user_prompt")
    if not user_prompt:
        return JSONResponse({"error": "Missing query"}, status_code=400)

    # Determine user_id
    user_id = None
    if isinstance(data.get("user"), dict):
        user_id = data["user"].get("id")
    user_id = user_id or data.get("user_id") or os.environ.get("ASSISTANT_DEFAULT_USER_ID")

    if not user_id:
        return JSONResponse({"error": "Missing user_id"}, status_code=400)

    # Transform the query and generate embedding
    query_obj = llama_query_transform(user_prompt)
    search_query = query_obj.get("query") or user_prompt
    search_filters = query_obj.get("filters") or {}

    try:
        embedding = embed_text(search_query)
    except Exception as e:
        return JSONResponse({"error": f"Embedding failed: {e}"}, status_code=500)

    tool_args = {
        "embedding": embedding,
        "user_id_filter": user_id,
        "file_name_filter": search_filters.get("file_name"),
        "description_filter": search_filters.get("description"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "search_query": search_query,
        "user_prompt": user_prompt,
    }

    results = perform_search(tool_args)
    matches = results.get("retrieved_chunks", [])

    # Neighbor attachment (kept for continuity)
    all_chunks_by_file = {}
    for chunk in matches:
        fid = chunk.get("file_id")
        if fid not in all_chunks_by_file:
            all_chunks_by_file[fid] = []
        all_chunks_by_file[fid].append(chunk)

    for chunk in matches:
        prev_chunk, next_chunk = get_neighbor_chunks(chunk, all_chunks_by_file)
        if prev_chunk:
            chunk["prev_chunk"] = {"content": f"[PREV] {prev_chunk.get('content', '')}"}
        if next_chunk:
            chunk["next_chunk"] = {"content": f"[NEXT] {next_chunk.get('content', '')}"}

    # Build lightweight response
    max_chunks = int(data.get("max_chunks", 50))
    excerpt_length = int(data.get("excerpt_length", 1000))
    documents = []

    for chunk in matches[:max_chunks]:
        text = (chunk.get("content") or "").replace("\n", " ").strip()
        if text:
            documents.append({
                "file_name": chunk.get("file_name"),
                "page_number": chunk.get("page_number"),
                "excerpt": text[:excerpt_length],
            })

    return JSONResponse({"documents": documents})


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
    """
    Simplified search endpoint for direct backend use — no LLM summarization.
    """
    data = await request.json()
    user_prompt = data.get("query") or data.get("user_prompt")
    user_id = data.get("user_id")

    if not user_prompt or not user_id:
        return JSONResponse({"error": "Missing query or user_id"}, status_code=400)

    query_obj = llama_query_transform(user_prompt)
    search_query = query_obj.get("query") or user_prompt
    search_filters = query_obj.get("filters") or {}

    embedding = embed_text(search_query)
    tool_args = {
        "embedding": embedding,
        "user_id_filter": user_id,
        "file_name_filter": search_filters.get("file_name"),
        "description_filter": search_filters.get("description"),
        "search_query": search_query,
    }

    results = perform_search(tool_args)
    matches = results.get("retrieved_chunks", [])

    documents = []
    for chunk in matches[:50]:
        text = (chunk.get("content") or "").replace("\n", " ").strip()
        if text:
            documents.append({
                "file_name": chunk.get("file_name"),
                "page_number": chunk.get("page_number"),
                "excerpt": text[:1000],
            })

    return JSONResponse({"documents": documents})
