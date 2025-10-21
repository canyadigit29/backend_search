import json
import os
from collections import defaultdict
import sys
import statistics
import httpx

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.openai_client import chat_completion, stream_chat_completion
from app.core.query_understanding import extract_search_filters
from app.core.llama_query_transform import llama_query_transform
from app.core.token_utils import trim_texts_to_token_limit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import time
from app.core.stopwatch import Stopwatch


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
SUPABASE_BUCKET_ID = "files"  # Hardcoded bucket name for consistency
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

def _fetch_chunks_by_ids(ids: list[str]):
    if not ids:
        return []
    try:
        # Fetch minimal fields needed to build sources and summary
        res = (
            supabase.table("document_chunks")
            .select("id,file_id,file_name,page_number,chunk_index,content")
            .in_("id", ids)
            .execute()
        )
        data = getattr(res, "data", None) or []
        return data
    except Exception:
        return []

def _select_included_and_pending(matches: list[dict], included_limit: int = 25, per_file_cap: int = 2):
    """
    From a score-sorted list of matches, select up to `included_limit` items with a
    per-file cap of `per_file_cap` to promote diversity. Operates over the top-50 only.
    Returns (included_chunks, pending_chunk_ids) where pending are the remaining items
    from the top-50 that were not included this pass, preserving order.
    """
    top50 = matches[:50]
    included = []
    included_ids = set()
    per_file_counts = defaultdict(int)

    # First pass: enforce per-file cap while filling included list
    for c in top50:
        if len(included) >= included_limit:
            break
        fid = c.get("file_id")
        cid = c.get("id")
        if not cid:
            continue
        if per_file_counts[fid] < per_file_cap:
            included.append(c)
            included_ids.add(cid)
            per_file_counts[fid] += 1

    # Second pass: if fewer than included_limit, fill from remaining without cap
    if len(included) < included_limit:
        for c in top50:
            if len(included) >= included_limit:
                break
            cid = c.get("id")
            if not cid or cid in included_ids:
                continue
            included.append(c)
            included_ids.add(cid)

    # Pending are the rest of top50 not included
    pending_ids = [c.get("id") for c in top50 if c.get("id") and c.get("id") not in included_ids]
    return included, pending_ids

def perform_search(tool_args):
    query_embedding = tool_args.get("embedding")
    file_name_filter = tool_args.get("file_name_filter")
    description_filter = tool_args.get("description_filter")
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")
    user_id_filter = tool_args.get("user_id_filter")
    user_prompt = tool_args.get("user_prompt")
    search_query = tool_args.get("search_query")
    
    # --- Dynamic Relevance Parameters ---
    threshold = tool_args.get("relevance_threshold", 0.4) # Similarity threshold
    max_results = tool_args.get("max_results", 100) # Max results to return

    # Patch: If embedding is missing, generate it from search_query or user_prompt
    if not query_embedding:
        text_to_embed = search_query or user_prompt
        if not text_to_embed:
            return {"error": "Embedding must be provided to perform similarity search."}
        from app.api.file_ops.embed import embed_text
        query_embedding = embed_text(text_to_embed)

    if not user_id_filter:
        return {"error": "user_id must be provided to perform search."}
    try:
        # --- ALIGNMENT: Pass the relevance_threshold from the assistant directly to the DB ---
        # The 'relevance_threshold' from the assistant is a SIMILARITY score (0 to 1).
        # The DB function 'match_documents' expects a DISTANCE (1 - similarity).
        db_match_threshold = 1 - threshold

        # Semantic search
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": user_id_filter,
            "file_name_filter": file_name_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date,
            "match_threshold": db_match_threshold, # Use the calculated distance threshold
            "match_count": 150
        }
        # Add metadata filters
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
        
        matches = response.data or []
        
        # --- ALIGNMENT: Remove complex python-side scoring. Trust the DB score. ---
        # The database 'score' is the similarity score, which is what we want.
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        # --- Apply max_results limit after sorting ---
        matches = matches[:max_results]

        return {"retrieved_chunks": matches}
    except Exception as e:
        return {"error": f"Error during search: {str(e)}"}


def extract_search_query(user_prompt: str, agent_mode: bool = False) -> str:
    """
    Use OpenAI to extract the most effective search phrase or keywords for semantic document retrieval from the user's request.
    If agent_mode is True, use enhanced instructions for extraction.
    """
    if agent_mode:
        system_prompt = (
            "You are a search assistant. Given a user's request, extract only the most relevant keywords or noun phrases that would best match the content of documents in a search system.\n"
            "- Do not include generic phrases like \"reference to,\" \"information about,\" \"how,\" or \"give me a rundown.\"\n"
            "- Only return concise, specific terms or noun phrases likely to appear in documents.\n"
            "- Use terminology that matches how topics are described in official documents.\n"
            "- Do not include instructions, explanations, or conversational words.\n"
            "- Separate each keyword or phrase with a comma.\n\n"
            "Examples:\n"
            "User: search for reference to arpa and give me a rundown of how the money was used\n"
            "Extracted: ARPA funding usage, ARPA money allocation\n\n"
            "User: find documents about covid relief funding\n"
            "Extracted: covid relief funding\n\n"
            "User: show me reports on infrastructure spending in 2022\n"
            "Extracted: infrastructure spending 2022\n\n"
            "User: what are the guidelines for grant applications\n"
            "Extracted: grant application guidelines\n"
        )
    else:
        system_prompt = (
            "You are a helpful assistant. Given a user request, extract a search phrase or keywords that would best match the content of relevant documents in a semantic search system. "
            "Be specific and use terminology likely to appear in the documents. "
            "Return only the search phrase or keywords, not instructions or explanations."
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    result = chat_completion(messages)
    return result.strip()


async def semantic_search(request, payload):
    return perform_search(payload)


router = APIRouter()


def keyword_search(keywords, user_id_filter=None, file_name_filter=None, description_filter=None, start_date=None, end_date=None, match_count=150):
    """
    Keyword search over document_chunks table using Postgres FTS (ts_rank/BM25).
    Returns chunks containing any of the keywords, with a ts_rank score.
    """
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
        response = httpx.post(endpoint, headers=headers, json=rpc_args, timeout=30)
        response.raise_for_status()
        results = response.json() or []
        for r in results:
            r["keyword_score"] = r.get("ts_rank", 0)
        return results
    except Exception as e:
        return []


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
    # This endpoint is now a simplified wrapper around the assistant endpoint logic
    data = await request.json()
    return await assistant_search_docs(request)


# Endpoint to accept calls from an OpenAI Assistant (custom function / webhook)
@router.post("/assistant/search_docs")
async def assistant_search_docs(request: Request):
    """
    Accepts a payload from an OpenAI assistant, normalizes fields, and forwards
    to the perform_search flow.
    """
    # Timer no longer controls batching; kept available if needed elsewhere
    sw = None

    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON payload: {e}"}, status_code=400)

    user_prompt = data.get("query") or data.get("user_prompt")
    if not user_prompt:
        return JSONResponse({"error": "Missing query in payload"}, status_code=400)

    user_id = (data.get("user", {}).get("id") or 
               os.environ.get("ASSISTANT_DEFAULT_USER_ID") or 
               "4a867500-7423-4eaa-bc79-94e368555e05")

    # Optional resume mode: summarize only specific chunk IDs provided by the caller
    resume_chunk_ids = data.get("resume_chunk_ids")

    query_obj = llama_query_transform(user_prompt)
    search_query = query_obj.get("query") or user_prompt
    search_filters = query_obj.get("filters") or {}

    try:
        embedding = embed_text(search_query)
    except Exception as e:
        return JSONResponse({"error": f"Failed to generate embedding: {e}"}, status_code=500)

    matches = []
    if resume_chunk_ids:
        # Resume path: fetch only the requested chunk IDs, preserving the caller-provided order
        fetched = _fetch_chunks_by_ids(resume_chunk_ids)
        by_id = {c.get("id"): c for c in fetched}
        matches = [by_id[i] for i in resume_chunk_ids if by_id.get(i)]
    else:
        tool_args = {
            "embedding": embedding,
            "user_id_filter": user_id,
            "file_name_filter": search_filters.get("file_name") or data.get("file_name_filter"),
            "description_filter": search_filters.get("description") or data.get("description_filter"),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "user_prompt": user_prompt,
            "search_query": search_query,
            "relevance_threshold": data.get("relevance_threshold"),
            "max_results": data.get("max_results")
        }
        for meta_field in ["document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title"]:
            if search_filters.get(meta_field) is not None:
                tool_args[meta_field] = search_filters[meta_field]
        
        # Optional OR-terms merging: if provided, run per-term searches and merge by ID using max score
        or_terms = data.get("or_terms") or []
        if or_terms and isinstance(or_terms, list):
            merged_by_id: dict[str, dict] = {}
            for term in or_terms:
                term = (term or "").strip()
                if not term:
                    continue
                try:
                    term_embedding = embed_text(term)
                except Exception:
                    # Skip a term if embedding fails
                    continue
                term_args = {**tool_args, "embedding": term_embedding, "search_query": term}
                term_result = perform_search(term_args)
                term_matches = term_result.get("retrieved_chunks", []) if isinstance(term_result, dict) else []
                for m in term_matches:
                    mid = m.get("id")
                    if not mid:
                        continue
                    best = merged_by_id.get(mid)
                    if best is None or (m.get("score", 0) or 0) > (best.get("score", 0) or 0):
                        merged_by_id[mid] = m
            matches = sorted(merged_by_id.values(), key=lambda x: x.get("score", 0), reverse=True)
            # Apply max_results cap if provided
            max_results = data.get("max_results") or 100
            matches = matches[:max_results]
        else:
            search_result = perform_search(tool_args)
            matches = search_result.get("retrieved_chunks", [])
    
    # --- OPTIMIZED: Neighbor retrieval and summary using the simplified results ---
    chunk_map = {(c.get("file_id"), c.get("chunk_index")): c for c in matches}
    for chunk in matches:
        file_id, chunk_index = chunk.get("file_id"), chunk.get("chunk_index")
        if file_id and chunk_index is not None:
            prev_chunk = chunk_map.get((file_id, chunk_index - 1))
            if prev_chunk: chunk["prev_chunk"] = {k: prev_chunk[k] for k in ("content", "page_number") if k in prev_chunk}
            next_chunk = chunk_map.get((file_id, chunk_index + 1))
            if next_chunk: chunk["next_chunk"] = {k: next_chunk[k] for k in ("content", "page_number") if k in next_chunk}

    summary = None
    summary_was_partial = False
    pending_chunk_ids = []
    included_chunk_ids = []
    included_chunks = []
    try:
        # Fixed batching with diversification: select included with per-file cap, pending is the rest of top 50
        included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=25, per_file_cap=2)
        included_chunk_ids = [c.get("id") for c in included_chunks if c.get("id")]

        # Guardrail: trim each chunk to avoid exceeding model context (hard-coded)
        per_chunk_char_limit = 2500
        def _trim(s: str, n: int) -> str:
            if not s:
                return ""
            return s[:n]
        # Build structured search results block with metadata headers per chunk
        annotated_texts = []
        for idx, chunk in enumerate(included_chunks, start=1):
            header = f"[#${idx} id={chunk.get('id')} file={chunk.get('file_name')} page={chunk.get('page_number')} score={round(chunk.get('score') or 0, 4)}]"
            body = _trim(chunk.get("content", ""), per_chunk_char_limit)
            if body:
                annotated_texts.append(f"{header}\n{body}")
        # Token-aware cap across all included texts (hard-coded): assume 200k+ token context, leave headroom
        MAX_INPUT_TOKENS = 260_000
        top_text = trim_texts_to_token_limit(annotated_texts, MAX_INPUT_TOKENS, model="gpt-5", separator="\n\n")
        
        if top_text.strip():
            summary_prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are an insightful research assistant. Read the provided document chunks and produce a concise, accurate synthesis that directly answers the user's query. "
                        "Cite evidence using the chunk ids (id=...) when making claims. Prefer precision over verbosity. If multiple interpretations exist, explain them briefly."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User query: {user_prompt}\n\n"
                        "Search results (each chunk starts with a metadata header):\n"
                        f"{top_text}\n\n"
                        "Please respond with the following structure:\n"
                        "1) Key findings (with inline citations like [id=...])\n"
                        "2) Evidence by chunk (grouped by id, 1-3 bullets each)\n"
                        "3) Important names/aliases/variants\n"
                        "4) Suggested follow-up questions"
                    ),
                },
            ]
            # No time cap: complete the summary for this batch; constrain output tokens (hard-coded)
            MAX_OUTPUT_TOKENS = 100_000
            content, was_partial = stream_chat_completion(summary_prompt, model=None, max_seconds=99999, max_tokens=MAX_OUTPUT_TOKENS)
            summary = content if content else None
            summary_was_partial = bool(was_partial)
            # Even if partial occurs due to upstream issues, we still return pending based on batch remainder
    except Exception:
        summary = None
        summary_was_partial = False
        # Keep pending/included as computed if any
    
    # --- Generate signed URLs for each source ---
    excerpt_length = 300
    sources = []
    # Iterate over the same 'included_chunks' list to build the sources we summarized.
    for c in included_chunks:
        file_name = c.get("file_name")
        signed_url = None
        if file_name:
            try:
                # Create a temporary, secure download link valid for 5 minutes.
                res = supabase.storage.from_(SUPABASE_BUCKET_ID).create_signed_url(file_name, 300)
                signed_url = res.get('signedURL')
            except Exception:
                signed_url = None # Fail gracefully if URL generation fails

        content = c.get("content") or ""
        excerpt = content.strip().replace("\n", " ")[:excerpt_length]
        sources.append({
            "id": c.get("id"),
            "file_name": file_name,
            "page_number": c.get("page_number"),
            "score": c.get("score"),
            "excerpt": excerpt,
            "url": signed_url # Add the new URL field
        })

    # Resume is offered when there are remainder chunks in the top-50 (batch 2)
    can_resume = bool(pending_chunk_ids)
    return JSONResponse({
        "summary": summary,
        "summary_was_partial": summary_was_partial,
        "sources": sources,
        "can_resume": can_resume,
        "pending_chunk_ids": pending_chunk_ids,
        "included_chunk_ids": included_chunk_ids,
        # Timing metrics removed for batch-based flow
    })


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
