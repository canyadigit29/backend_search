import json
import os
from collections import defaultdict
import sys
import statistics
import httpx

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.openai_client import chat_completion
from app.core.query_understanding import extract_search_filters
from app.core.llama_query_transform import llama_query_transform

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import tiktoken


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
SUPABASE_BUCKET_ID = "files"  # Hardcoded bucket name for consistency
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

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
        "relevance_threshold": data.get("relevance_threshold"),
        "max_results": data.get("max_results")
    }
    for meta_field in ["document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title"]:
        if search_filters.get(meta_field) is not None:
            tool_args[meta_field] = search_filters[meta_field]

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
    try:
        # The chunks used for the summary are now the definitive list of sources.
        summary_chunks = matches[:50]
        top_texts = [chunk.get("content", "") for chunk in summary_chunks if chunk.get("content")]
        top_text = "\n\n".join(top_texts)
        
        if top_text.strip():
            summary_prompt = [
                {"role": "system", "content": "You are an insightful assistant. Using the following search results, answer the user's query clearly and concisely. Synthesize, interpret, and connect the information. Prioritize accuracy, relevance, and clarity. If results are ambiguous, state your reasoning and cite the most relevant sources."},
                {"role": "user", "content": f"User query: {user_prompt}\n\nSearch results:\n{top_text}"}
            ]
            summary = chat_completion(summary_prompt, model="gpt-5")
    except Exception:
        summary = None
    
    # --- Generate signed URLs for each source ---
    excerpt_length = 300
    sources = []
    # Iterate over the same 'summary_chunks' list to build the sources.
    for c in summary_chunks:
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

    return JSONResponse({"summary": summary, "sources": sources})


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
