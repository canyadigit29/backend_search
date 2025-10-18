import json
import os
from collections import defaultdict
import sys
import statistics
import httpx
import re

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.query_understanding import extract_search_filters
from app.core.llama_query_transform import llama_query_transform

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# Environment / clients
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

# Constants tuned for client-side summarization
MAX_SUMMARY_TOKENS = int(os.environ.get("MAX_SUMMARY_TOKENS", 150_000))  # kept for compatibility but not used for server summarization
MAX_SUMMARY_SOURCES = int(os.environ.get("MAX_SUMMARY_SOURCES", 50))     # sources returned for frontend (non-compact)
DEFAULT_COMPACT_MAX_CHUNKS = int(os.environ.get("DEFAULT_COMPACT_MAX_CHUNKS", 25))
DEFAULT_EXCERPT_LENGTH = int(os.environ.get("DEFAULT_EXCERPT_LENGTH", 300))
FINAL_SCORE_THRESHOLD = float(os.environ.get("FINAL_SCORE_THRESHOLD", 0.4))

router = APIRouter()


def perform_search(tool_args):
    """
    Perform semantic + keyword hybrid search and return matched chunks.
    Expects tool_args to include at least:
      - embedding (list or vector) OR search_query/user_prompt to create embedding
      - user_id_filter
    Returns dict: {"retrieved_chunks": [...]} or {"error": "..."}
    """
    query_embedding = tool_args.get("embedding")
    expected_phrase = tool_args.get("expected_phrase")
    file_name_filter = tool_args.get("file_name_filter")
    description_filter = tool_args.get("description_filter")
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")
    user_id_filter = tool_args.get("user_id_filter")
    user_prompt = tool_args.get("user_prompt")
    search_query = tool_args.get("search_query")

    # If embedding is missing, generate it from search_query or user_prompt
    if not query_embedding:
        text_to_embed = search_query or user_prompt
        if not text_to_embed:
            return {"error": "Embedding must be provided to perform similarity search."}
        query_embedding = embed_text(text_to_embed)

    if not user_id_filter:
        return {"error": "user_id must be provided to perform search."}

    try:
        # Prepare RPC args for Supabase vector match
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": user_id_filter,
            "file_name_filter": file_name_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date,
            "match_threshold": tool_args.get("match_threshold", 0.5),
            "match_count": tool_args.get("match_count", 300)
        }

        # Add metadata filters with filter_ prefix for SQL compatibility (if provided)
        metadata_fields = [
            ("meeting_year", "filter_meeting_year"),
            ("meeting_month", "filter_meeting_month"),
            ("meeting_month_name", "filter_meeting_month_name"),
            ("meeting_day", "filter_meeting_day"),
            ("document_type", "filter_document_type"),
            ("ordinance_title", "filter_ordinance_title"),
        ]
        for tool_key, rpc_key in metadata_fields:
            if tool_args.get(tool_key) is not None:
                rpc_args[rpc_key] = tool_args[tool_key]

        response = supabase.rpc("match_documents", rpc_args).execute()
        if getattr(response, "error", None):
            return {"error": f"Supabase RPC failed: {response.error.message}"}
        semantic_matches = response.data or []

        # Sort descending by score (Supabase RPC may already return sorted)
        semantic_matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Hybrid: combine with keyword/FTS results (no multiplicative boosting here, just union)
        if not search_query and user_prompt:
            search_query = user_prompt
        stopwords = {
            "the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with",
            "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"
        }
        keywords = [w for w in re.split(r"\W+", search_query or "") if w and w.lower() not in stopwords]

        # Call local keyword_search (defined below)
        keyword_results = keyword_search(keywords, user_id_filter=user_id_filter)

        # Merge semantic and keyword results (union by id)
        all_matches = {m["id"]: m for m in semantic_matches}
        for k in keyword_results:
            if k["id"] not in all_matches:
                all_matches[k["id"]] = k

        # Compute keyword_score (normalized) and semantic_score (normalized)
        def count_keyword_hits(text, keywords_list):
            if not keywords_list:
                return 0
            text_lower = (text or "").lower()
            return sum(1 for kw in keywords_list if kw.lower() in text_lower)

        # Assign keyword_score raw values (number of hits or ts_rank from FTS)
        max_keyword_hits = 1
        for m in all_matches.values():
            if m.get("keyword_score") is not None:
                hits = m.get("keyword_score", 0)
            else:
                hits = count_keyword_hits(m.get("content", ""), keywords)
            m["keyword_score"] = hits
            if hits > max_keyword_hits:
                max_keyword_hits = hits

        # Normalize keyword_score to [0,1]
        for m in all_matches.values():
            m["keyword_score"] = float(m.get("keyword_score", 0)) / max_keyword_hits if max_keyword_hits > 0 else 0.0

        # Normalize semantic score to [0,1]
        semantic_scores = [m.get("score", 0) for m in all_matches.values()]
        min_sem = min(semantic_scores) if semantic_scores else 0
        max_sem = max(semantic_scores) if semantic_scores else 1
        for m in all_matches.values():
            raw = m.get("score", 0)
            if max_sem > min_sem:
                m["semantic_score"] = (raw - min_sem) / (max_sem - min_sem)
            else:
                m["semantic_score"] = 1.0 if raw > 0 else 0.0

        # Weighted final_score (semantic vs keyword)
        alpha = float(tool_args.get("semantic_weight", 0.6))
        beta = float(tool_args.get("keyword_weight", 0.4))
        for m in all_matches.values():
            m["final_score"] = alpha * m.get("semantic_score", 0.0) + beta * m.get("keyword_score", 0.0)

        # Produce final result list, apply a threshold to remove weak matches, sort by final_score
        matches = [m for m in all_matches.values() if m.get("final_score", 0.0) >= float(tool_args.get("final_threshold", FINAL_SCORE_THRESHOLD))]
        matches.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)

        # Limit to top N matches to keep downstream processing bounded
        matches = matches[: tool_args.get("max_return", 100) or 100]

        return {"retrieved_chunks": matches}
    except Exception as e:
        return {"error": f"Error during search: {str(e)}"}


async def semantic_search(request, payload):
    return perform_search(payload)


def keyword_search(keywords, user_id_filter=None, file_name_filter=None, description_filter=None, start_date=None, end_date=None, match_count=300):
    """
    Keyword search over document_chunks table using Postgres FTS (ts_rank/BM25).
    Returns chunks containing any of the keywords, with a ts_rank score.
    """
    if not keywords:
        return []

    keyword_query = " ".join(keywords)
    rpc_args = {
        "keyword_query": keyword_query,
        "user_id_filter": user_id_filter,
        "file_name_filter": file_name_filter,
        "description_filter": description_filter,
        "match_count": match_count
    }
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_role = os.environ["SUPABASE_SERVICE_ROLE"]
    endpoint = f"{supabase_url}/rest/v1/rpc/match_documents_fts"
    headers = {
        "apikey": service_role,
        "Authorization": f"Bearer {service_role}",
        "Content-Type": "application/json"
    }
    try:
        response = httpx.post(endpoint, headers=headers, json=rpc_args, timeout=120)
        response.raise_for_status()
        results = response.json() or []
        for r in results:
            if "ts_rank" in r and r.get("ts_rank") is not None:
                r["keyword_score"] = r.get("ts_rank", 0)
            else:
                r["keyword_score"] = 0
        return results
    except Exception:
        return []


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


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
    """
    Main public search endpoint used by frontend.
    Server no longer performs LLM summarization — it returns compact sources (excerpts)
    and lets the assistant/client produce the summary. This avoids the 60s action timeout.
    """
    data = await request.json()
    user_prompt = data.get("query") or data.get("user_prompt")
    user_id = data.get("user_id")
    if not user_prompt:
        return JSONResponse({"error": "Missing query"}, status_code=400)
    if not user_id:
        return JSONResponse({"error": "Missing user_id"}, status_code=400)

    query_obj = llama_query_transform(user_prompt)
    search_query = query_obj.get("query") or user_prompt
    search_filters = query_obj.get("filters") or {}

    try:
        embedding = embed_text(search_query)
    except Exception as e:
        return JSONResponse({"error": f"Failed to generate embedding: {e}"}, status_code=500)

    tool_args = {
        "embedding": embedding,
        "user_id_filter": user_id,
        "file_name_filter": search_filters.get("file_name") or data.get("file_name_filter"),
        "description_filter": search_filters.get("description") or data.get("description_filter"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "user_prompt": user_prompt,
        "search_query": search_query,
        "semantic_weight": data.get("semantic_weight", 0.6),
        "keyword_weight": data.get("keyword_weight", 0.4),
        "final_threshold": data.get("final_threshold", FINAL_SCORE_THRESHOLD),
        "max_return": data.get("max_return", 100)
    }
    for meta_field in [
        "document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title", "file_extension", "section_header", "page_number"
    ]:
        if search_filters.get(meta_field) is not None:
            tool_args[meta_field] = search_filters[meta_field]

    semantic_result = perform_search(tool_args)
    if semantic_result.get("error"):
        return JSONResponse(semantic_result, status_code=500)
    matches = semantic_result.get("retrieved_chunks", [])

    # Neighbor chunk retrieval
    all_chunks_by_file = {}
    for chunk in matches:
        file_id = chunk.get("file_id")
        if file_id not in all_chunks_by_file:
            all_chunks_by_file[file_id] = []
        all_chunks_by_file[file_id].append(chunk)
    for chunk in matches:
        prev_chunk, next_chunk = get_neighbor_chunks(chunk, all_chunks_by_file)
        if prev_chunk:
            chunk["prev_chunk"] = {k: prev_chunk[k] for k in ("id", "chunk_index", "content", "section_header", "page_number") if k in prev_chunk}
        if next_chunk:
            chunk["next_chunk"] = {k: next_chunk[k] for k in ("id", "chunk_index", "content", "section_header", "page_number") if k in next_chunk}

    # Postprocess: dedupe and expand neighbors
    def postprocess_chunks(chunks, expand_neighbors=True, deduplicate=True, custom_filter=None):
        if deduplicate:
            seen = set()
            unique_chunks = []
            for c in chunks:
                content = c.get("content", "")
                if content not in seen:
                    seen.add(content)
                    unique_chunks.append(c)
            chunks = unique_chunks
        if expand_neighbors:
            expanded = []
            for c in chunks:
                expanded.append(c)
                if c.get("prev_chunk"):
                    prev = c["prev_chunk"]
                    prev_copy = prev.copy()
                    prev_copy["content"] = f"[PREV] {prev_copy.get('content','')}"
                    expanded.append(prev_copy)
                if c.get("next_chunk"):
                    nxt = c["next_chunk"]
                    nxt_copy = nxt.copy()
                    nxt_copy["content"] = f"[NEXT] {nxt_copy.get('content','')}"
                    expanded.append(nxt_copy)
            chunks = expanded
        if custom_filter:
            chunks = [c for c in chunks if custom_filter(c)]
        return chunks

    matches = postprocess_chunks(matches, expand_neighbors=True, deduplicate=True)

    # Build compact sources for client-side summarization (no server LLM calls)
    compact = data.get("compact") if isinstance(data, dict) else None
    if compact is None:
        compact = True

    try:
        max_chunks = int(data.get("max_chunks", DEFAULT_COMPACT_MAX_CHUNKS)) if isinstance(data, dict) and data.get("max_chunks") is not None else DEFAULT_COMPACT_MAX_CHUNKS
    except Exception:
        max_chunks = DEFAULT_COMPACT_MAX_CHUNKS
    try:
        excerpt_length = int(data.get("excerpt_length", DEFAULT_EXCERPT_LENGTH)) if isinstance(data, dict) and data.get("excerpt_length") is not None else DEFAULT_EXCERPT_LENGTH
    except Exception:
        excerpt_length = DEFAULT_EXCERPT_LENGTH

    sorted_matches = sorted(matches, key=lambda x: x.get("final_score", x.get("score", 0)), reverse=True)
    # Sources consist of compact metadata + excerpt only (safe for sending to assistant)
    sources = []
    for c in sorted_matches[:max_chunks]:
        content = (c.get("content") or "")
        excerpt = content.strip().replace("\n", " ")[:excerpt_length]
        src = {
            "id": c.get("id"),
            "file_name": c.get("file_name"),
            "page_number": c.get("page_number"),
            "final_score": c.get("final_score"),
            "excerpt": excerpt
        }
        sources.append(src)

    # Return compact response: assistant/client will create the summary
    return JSONResponse({"summary": None, "sources": sources})


@router.post("/assistant/search_docs")
async def assistant_search_docs(request: Request):
    """
    Endpoint used by OpenAI assistant (function/webhook).
    Server no longer performs LLM summarization — it returns compact sources for the assistant to summarize.
    """
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON payload: {e}"}, status_code=400)

    # Normalize assistant payload to extract user prompt
    user_prompt = data.get("query") or data.get("input") or data.get("user_prompt")
    if not user_prompt:
        conv = data.get("conversation") or data.get("messages") or {}
        if isinstance(conv, list):
            for msg in reversed(conv):
                if msg.get("role") == "user" and msg.get("content"):
                    user_prompt = msg.get("content")
                    break
        elif isinstance(conv, dict):
            user_prompt = conv.get("last_user_message") or conv.get("content") or user_prompt

    if not user_prompt:
        return JSONResponse({"error": "Missing query in assistant payload"}, status_code=400)

    # Determine user id; fall back to assistant service account id if necessary
    user_id = None
    if isinstance(data.get("user"), dict):
        user_id = data.get("user").get("id")
    user_id = user_id or data.get("user_id") or data.get("userId")
    if not user_id:
        user_id = os.environ.get("ASSISTANT_DEFAULT_USER_ID") or "4a867500-7423-4eaa-bc79-94e368555e05"

    query_obj = llama_query_transform(user_prompt)
    search_query = query_obj.get("query") or user_prompt
    search_filters = query_obj.get("filters") or {}

    try:
        embedding = embed_text(search_query)
    except Exception as e:
        return JSONResponse({"error": f"Failed to generate embedding: {e}"}, status_code=500)

    tool_args = {
        "embedding": embedding,
        "user_id_filter": user_id,
        "file_name_filter": search_filters.get("file_name") or data.get("file_name_filter") or None,
        "description_filter": search_filters.get("description") or data.get("description_filter") or None,
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "user_prompt": user_prompt,
        "search_query": search_query,
        "semantic_weight": data.get("semantic_weight", 0.6),
        "keyword_weight": data.get("keyword_weight", 0.4),
        "final_threshold": data.get("final_threshold", FINAL_SCORE_THRESHOLD),
        "max_return": data.get("max_return", 100)
    }
    for meta_field in [
        "document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title", "file_extension", "section_header", "page_number"
    ]:
        if search_filters.get(meta_field) is not None:
            tool_args[meta_field] = search_filters[meta_field]

    semantic_result = perform_search(tool_args)
    if semantic_result.get("error"):
        return JSONResponse(semantic_result, status_code=500)
    matches = semantic_result.get("retrieved_chunks", [])

    # Neighbor chunk retrieval
    all_chunks_by_file = {}
    for chunk in matches:
        file_id = chunk.get("file_id")
        if file_id not in all_chunks_by_file:
            all_chunks_by_file[file_id] = []
        all_chunks_by_file[file_id].append(chunk)
    for chunk in matches:
        prev_chunk, next_chunk = get_neighbor_chunks(chunk, all_chunks_by_file)
        if prev_chunk:
            chunk["prev_chunk"] = {k: prev_chunk[k] for k in ("id", "chunk_index", "content", "section_header", "page_number") if k in prev_chunk}
        if next_chunk:
            chunk["next_chunk"] = {k: next_chunk[k] for k in ("id", "chunk_index", "content", "section_header", "page_number") if k in next_chunk}

    # Postprocess: dedupe and expand neighbors
    def postprocess_chunks(chunks, expand_neighbors=True, deduplicate=True, custom_filter=None):
        if deduplicate:
            seen = set()
            unique_chunks = []
            for c in chunks:
                content = c.get("content", "")
                if content not in seen:
                    seen.add(content)
                    unique_chunks.append(c)
            chunks = unique_chunks
        if expand_neighbors:
            expanded = []
            for c in chunks:
                expanded.append(c)
                if c.get("prev_chunk"):
                    prev = c["prev_chunk"]
                    prev_copy = prev.copy()
                    prev_copy["content"] = f"[PREV] {prev_copy.get('content','')}"
                    expanded.append(prev_copy)
                if c.get("next_chunk"):
                    nxt = c["next_chunk"]
                    nxt_copy = nxt.copy()
                    nxt_copy["content"] = f"[NEXT] {nxt_copy.get('content','')}"
                    expanded.append(nxt_copy)
            chunks = expanded
        if custom_filter:
            chunks = [c for c in chunks if custom_filter(c)]
        return chunks

    matches = postprocess_chunks(matches, expand_neighbors=True, deduplicate=True)

    # Build compact sources for assistant to summarize (server-side summarization disabled)
    compact = data.get("compact") if isinstance(data, dict) else None
    if compact is None:
        compact = True

    try:
        max_chunks = int(data.get("max_chunks", DEFAULT_COMPACT_MAX_CHUNKS)) if isinstance(data, dict) and data.get("max_chunks") is not None else DEFAULT_COMPACT_MAX_CHUNKS
    except Exception:
        max_chunks = DEFAULT_COMPACT_MAX_CHUNKS
    try:
        excerpt_length = int(data.get("excerpt_length", DEFAULT_EXCERPT_LENGTH)) if isinstance(data, dict) and data.get("excerpt_length") is not None else DEFAULT_EXCERPT_LENGTH
    except Exception:
        excerpt_length = DEFAULT_EXCERPT_LENGTH

    sorted_matches = sorted(matches, key=lambda x: x.get("final_score", x.get("score", 0)), reverse=True)
    sources = []
    for c in sorted_matches[:max_chunks]:
        content = (c.get("content") or "")
        excerpt = content.strip().replace("\n", " ")[:excerpt_length]
        src = {
            "id": c.get("id"),
            "file_name": c.get("file_name"),
            "page_number": c.get("page_number"),
            "final_score": c.get("final_score"),
            "excerpt": excerpt
        }
        sources.append(src)

    # Summary is handled by the assistant/client — return None
    return JSONResponse({"summary": None, "sources": sources})


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
