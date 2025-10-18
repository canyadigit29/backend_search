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
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

def perform_search(tool_args):
    query_embedding = tool_args.get("embedding")
    expected_phrase = tool_args.get("expected_phrase")
    file_name_filter = tool_args.get("file_name_filter")
    description_filter = tool_args.get("description_filter")
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")
    user_id_filter = tool_args.get("user_id_filter")
    user_prompt = tool_args.get("user_prompt")
    search_query = tool_args.get("search_query")

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
        # Semantic search
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": user_id_filter,
            "file_name_filter": file_name_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date,
            "match_threshold": tool_args.get("match_threshold", 0.5),  # Updated default threshold to 0.5 per latest score test
            "match_count": tool_args.get("match_count", 300)
        }
        # Add metadata filters with filter_ prefix for SQL compatibility
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
        if semantic_matches:
            pass
        else:
            pass
        semantic_matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Debug: print top 5 semantic matches before any boosting
        for i, m in enumerate(semantic_matches[:5]):
            preview = (m.get("content", "") or "")[:200].replace("\n", " ")
            # Removed debug print: [DEBUG] SEMANTIC ...
            pass

        # Hybrid/boosted merging and debug output
        import re
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        # Use search_query from user_prompt if available
        if not search_query and user_prompt:
            search_query = user_prompt
        keywords = [w for w in re.split(r"\W+", search_query or "") if w and w.lower() not in stopwords]
        from app.api.file_ops.search_docs import keyword_search
        keyword_results = keyword_search(keywords, user_id_filter=user_id_filter)
        all_matches = {m["id"]: m for m in semantic_matches}
        # Remove all additive boosting for phrase/keyword matches
        for k in keyword_results:
            if k["id"] not in all_matches:
                all_matches[k["id"]] = k
        matches = list(all_matches.values())
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        # --- Filter matches by metadata fields after merging ---
        metadata_fields = [
            ("meeting_year", "meeting_year"),
            ("meeting_month", "meeting_month"),
            ("meeting_month_name", "meeting_month_name"),
            ("meeting_day", "meeting_day"),
            ("document_type", "document_type"),
            ("ordinance_title", "ordinance_title"),
        ]
        for tool_key, match_key in metadata_fields:
            filter_value = tool_args.get(tool_key)
            if filter_value is not None:
                matches = [m for m in matches if m.get(match_key) == filter_value]

        # [DEBUG SCORES] Begin score analysis
        def avg(lst):
            return sum(lst) / len(lst) if lst else 0
        def med(lst):
            n = len(lst)
            if n == 0:
                return 0
            s = sorted(lst)
            return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2
        def score_stats(match_list):
            scores = [m.get("score", 0) for m in match_list]
            boosted = [m.get("score", 0) for m in match_list if m.get("boosted_reason")]
            non_boosted = [m.get("score", 0) for m in match_list if not m.get("boosted_reason")]
            return {
                "avg": avg(scores),
                "median": med(scores),
                "boosted_avg": avg(boosted),
                "boosted_count": len(boosted),
                "non_boosted_avg": avg(non_boosted),
                "non_boosted_count": len(non_boosted),
            }
        all_stats = score_stats(matches)
        top20_stats = score_stats(matches[:20])
        top10_stats = score_stats(matches[:10])
        # [DEBUG SCORES] End score analysis
        # New debug: print top 5 results with score and content preview, and boosting info
        for i, m in enumerate(matches[:5]):
            preview = (m.get("content", "") or "")[:200].replace("\n", " ")
            boost_info = ""
            if m.get("boosted_reason") == "exact_phrase":
                boost_info = f" [BOOSTED: exact phrase, orig_score={m.get('original_score', 'n/a')}]"
            elif m.get("boosted_reason") == "keyword_overlap":
                boost_info = f" [BOOSTED: keyword overlap, orig_score={m.get('original_score', 'n/a')}]"
            sim_score = m.get("score", 0)
            orig_score = m.get("original_score", sim_score)
            # Removed debug print: [DEBUG] #... | sim_score: ...
        # After boosting, print all boosted results
        boosted_results = [m for m in matches if m.get("boosted_reason")]
        if boosted_results:
            # Removed debug print: [DEBUG] Boosted results found: ...
            pass
        else:
            # Removed debug print: [DEBUG] No boosted results found.
            pass
        # Removed debug print: [DEBUG] matches for response: ...
        # Limit to top 100 results for all semantic/hybrid searches
        matches = matches[:100]
        # Removed debug print: [DEBUG] Returning ... matches (max 100) in perform_search.
        # --- Add keyword_score to each match ---
        # Count keyword hits for each match
        def count_keyword_hits(text, keywords):
            text_lower = text.lower()
            return sum(1 for kw in keywords if kw.lower() in text_lower)
        max_keyword_hits = 1
        for m in all_matches.values():
            hits = count_keyword_hits(m.get("content", ""), keywords)
            m["keyword_score"] = hits
            if hits > max_keyword_hits:
                max_keyword_hits = hits
        # Normalize keyword_score to [0, 1]
        for m in all_matches.values():
            m["keyword_score"] = m["keyword_score"] / max_keyword_hits if max_keyword_hits > 0 else 0

        # --- Normalize semantic score to [0, 1] ---
        semantic_scores = [m.get("score", 0) for m in all_matches.values()]
        min_sem = min(semantic_scores) if semantic_scores else 0
        max_sem = max(semantic_scores) if semantic_scores else 1
        for m in all_matches.values():
            raw = m.get("score", 0)
            m["semantic_score"] = (raw - min_sem) / (max_sem - min_sem) if max_sem > min_sem else 0

        # --- Weighted sum for final score ---
        alpha = 0.6  # weight for semantic (from LLM suggestion)
        beta = 0.4   # weight for keyword (from LLM suggestion)
        for m in all_matches.values():
            m["final_score"] = alpha * m["semantic_score"] + beta * m["keyword_score"]

        matches = list(all_matches.values())
        # Apply threshold to filter out weak matches
        threshold = 0.4  # from LLM suggestion
        matches = [m for m in matches if m["final_score"] >= threshold]
        matches.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        return {"retrieved_chunks": matches}
    except Exception as e:
        # Removed debug print: [DEBUG] perform_search exception: ...
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


def keyword_search(keywords, user_id_filter=None, file_name_filter=None, description_filter=None, start_date=None, end_date=None, match_count=300):
    """
    Keyword search over document_chunks table using Postgres FTS (ts_rank/BM25).
    Returns chunks containing any of the keywords, with a ts_rank score.
    """
    # Join keywords for query
    keyword_query = " ".join(keywords)
    rpc_args = {
        "keyword_query": keyword_query,
        "user_id_filter": user_id_filter,
        "file_name_filter": file_name_filter,
        "description_filter": description_filter,
        "match_count": match_count
    }
    # Manual HTTPX call to Supabase RPC endpoint with required headers
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
        # Attach the ts_rank as keyword_score for downstream use
        for r in results:
            r["keyword_score"] = r.get("ts_rank", 0)
        return results
    except Exception as e:
        # Optionally log the error
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
    print("[DEBUG] /file_ops/search_docs endpoint called", flush=True)
    data = await request.json()
    user_prompt = data.get("query") or data.get("user_prompt")
    user_id = data.get("user_id")
    if not user_prompt:
        return JSONResponse({"error": "Missing query"}, status_code=400)
    if not user_id:
        return JSONResponse({"error": "Missing user_id"}, status_code=400)
    # --- LlamaIndex-style query transform ---
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
        "search_query": search_query
    }
    for meta_field in [
        "document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title", "file_extension", "section_header", "page_number"
    ]:
        if search_filters.get(meta_field) is not None:
            tool_args[meta_field] = search_filters[meta_field]
    # --- Semantic search only (no hybrid) ---
    semantic_result = perform_search(tool_args)
    matches = semantic_result.get("retrieved_chunks", [])
    # --- Neighbor chunk retrieval ---
    # Group all returned chunks by file_id for neighbor lookup
    all_chunks_by_file = {}
    for chunk in matches:
        file_id = chunk.get("file_id")
        if file_id not in all_chunks_by_file:
            all_chunks_by_file[file_id] = []
        all_chunks_by_file[file_id].append(chunk)
    # For each chunk, attach prev/next chunk if available
    for chunk in matches:
        prev_chunk, next_chunk = get_neighbor_chunks(chunk, all_chunks_by_file)
        if prev_chunk:
            chunk["prev_chunk"] = {k: prev_chunk[k] for k in ("id", "chunk_index", "content", "section_header", "page_number") if k in prev_chunk}
        if next_chunk:
            chunk["next_chunk"] = {k: next_chunk[k] for k in ("id", "chunk_index", "content", "section_header", "page_number") if k in next_chunk}
    # --- Postprocessing: deduplication and neighbor expansion ---
    def postprocess_chunks(chunks, expand_neighbors=True, deduplicate=True, custom_filter=None):
        # Deduplicate by content
        if deduplicate:
            seen = set()
            unique_chunks = []
            for c in chunks:
                content = c.get("content", "")
                if content not in seen:
                    seen.add(content)
                    unique_chunks.append(c)
            chunks = unique_chunks
        # Expand neighbors (add prev/next chunk content inline)
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
        # Custom filter
        if custom_filter:
            chunks = [c for c in chunks if custom_filter(c)]
        return chunks

    # After neighbor chunk attachment, before summary:
    matches = postprocess_chunks(matches, expand_neighbors=True, deduplicate=True)
    # --- LLM-based summary of top search results ---
    summary = None
    filtered_chunks = []
    try:
        # GPT-5 supports a large context window; include as many top chunks as fit in ~60,000 chars
        encoding = tiktoken.get_encoding("cl100k_base")
        MAX_SUMMARY_TOKENS = 64000
        sorted_chunks = sorted(matches, key=lambda x: x.get("score", 0), reverse=True)
        top_texts = []
        total_tokens = 0
        used_chunk_ids = set()
        for chunk in sorted_chunks:
            content = chunk.get("content", "")
            if not content:
                continue
            chunk_tokens = len(encoding.encode(content))
            if total_tokens + chunk_tokens > MAX_SUMMARY_TOKENS:
                break
            top_texts.append(content)
            total_tokens += chunk_tokens
            used_chunk_ids.add(chunk.get("id"))
        top_text = "\n\n".join(top_texts)

        # Filter matches to only those used in the summary
        filtered_chunks = [chunk for chunk in sorted_chunks if chunk.get("id") in used_chunk_ids]
        print(f"[DEBUG] Returning {len(filtered_chunks)} chunks actually used in summary (token-based, max 64,000 tokens).", flush=True)

        # Limit to top 20 sources for frontend
        filtered_chunks = filtered_chunks[:50]
        print(f"[DEBUG] Returning {len(filtered_chunks)} chunks (max 50) actually used in summary.", flush=True)

        if top_text.strip():
            from app.core.openai_client import chat_completion
            summary_prompt = [
                {"role": "system", "content": (
                    "You are an insightful, engaging, and helpful assistant. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible, but don't be afraid to show some personality and offer your own analysis or perspective.\n"
                    "- Focus on information directly relevant to the user's question, but feel free to synthesize, interpret, and connect the dots.\n"
                    "- If there are patterns, trends, or notable points, highlight them and explain their significance.\n"
                    "- Use a conversational, engaging tone.\n"
                    "- Use bullet points, sections, or narrative as you see fit for clarity and impact.\n"
                    "- Reference file names, dates, or section headers where possible.\n"
                    "- Do not add information that is not present in the results, but you may offer thoughtful analysis, context, or commentary based on what is present.\n"
                    "- If the results are lengthy, provide a high-level summary first, then details.\n"
                    "- Your goal is to be genuinely helpful, insightful, and memorable—not just a calculator."
                )},
                {"role": "user", "content": f"User query: {user_prompt}\n\nSearch results:\n{top_text}"}
            ]
            summary = chat_completion(summary_prompt, model="gpt-5")
        else:
            pass
    except Exception as e:
        pass
        summary = None

    return JSONResponse({"retrieved_chunks": filtered_chunks, "summary": summary})


# Endpoint to accept calls from an OpenAI Assistant (custom function / webhook)
@router.post("/assistant/search_docs")
async def assistant_search_docs(request: Request):
    """
    Accepts a payload from an OpenAI assistant (function call or webhook). Normalizes fields
    and forwards to the existing perform_search flow. If the assistant does not provide a
    user_id, the endpoint will use ASSISTANT_DEFAULT_USER_ID environment variable as a fallback.
    Expected minimal payload shape (assistant):
    {
        "query": "user query text",
        "conversation": { ... optional ... },
        "metadata": { ... optional ... },
        "user": { "id": "uuid-or-empty" }  # optional
    }
    """
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON payload: {e}"}, status_code=400)

    # Normalize assistant payload
    # If the assistant provided a top-level 'query' or nested 'input' use it.
    user_prompt = data.get("query") or data.get("input") or data.get("user_prompt")
    # Try a few conversation locations commonly used by assistant function calls
    if not user_prompt:
        conv = data.get("conversation") or data.get("messages") or {}
        # If messages is a list, attempt to find the last user message
        if isinstance(conv, list):
            for msg in reversed(conv):
                if msg.get("role") == "user" and msg.get("content"):
                    user_prompt = msg.get("content")
                    break
        elif isinstance(conv, dict):
            user_prompt = conv.get("last_user_message") or conv.get("content") or user_prompt

    if not user_prompt:
        return JSONResponse({"error": "Missing query in assistant payload"}, status_code=400)

    # Determine user id: prefer explicit user.id, then top-level user_id, then env fallback
    user_id = None
    if isinstance(data.get("user"), dict):
        user_id = data.get("user").get("id")
    user_id = user_id or data.get("user_id") or data.get("userId")
    if not user_id:
        # Use hard-coded assistant key UUID as a fallback so the assistant can perform
        # searches even when it doesn't have access to the real user's Supabase UUID.
        # This acts as a stable 'assistant' service account.
        user_id = os.environ.get("ASSISTANT_DEFAULT_USER_ID") or "4a867500-7423-4eaa-bc79-94e368555e05"

    # Apply query transform (llama-based) to extract filters if possible
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
        "search_query": search_query
    }
    for meta_field in [
        "document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title", "file_extension", "section_header", "page_number"
    ]:
        if search_filters.get(meta_field) is not None:
            tool_args[meta_field] = search_filters[meta_field]

    # Forward to existing perform_search logic
    semantic_result = perform_search(tool_args)
    matches = semantic_result.get("retrieved_chunks", [])

    # Keep neighbor chunk behavior consistent with /file_ops/search_docs
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

    # Postprocess and summarize similar to existing endpoint
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

    summary = None
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        MAX_SUMMARY_TOKENS = 64000
        sorted_chunks = sorted(matches, key=lambda x: x.get("score", 0), reverse=True)
        top_texts = []
        total_tokens = 0
        used_chunk_ids = set()
        for chunk in sorted_chunks:
            content = chunk.get("content", "")
            if not content:
                continue
            chunk_tokens = len(encoding.encode(content))
            if total_tokens + chunk_tokens > MAX_SUMMARY_TOKENS:
                break
            top_texts.append(content)
            total_tokens += chunk_tokens
            used_chunk_ids.add(chunk.get("id"))
        top_text = "\n\n".join(top_texts)
        filtered_chunks = [chunk for chunk in sorted_chunks if chunk.get("id") in used_chunk_ids]
        filtered_chunks = filtered_chunks[:20]
        if top_text.strip():
            from app.core.openai_client import chat_completion
            summary_prompt = [
                {"role": "system", "content": (
                    "You are an insightful, engaging, and helpful assistant. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible, but don't be afraid to show some personality and offer your own analysis or perspective.\n"
                    "- Focus on information directly relevant to the user's question, but feel free to synthesize, interpret, and connect the dots.\n"
                    "- If there are patterns, trends, or notable points, highlight them and explain their significance.\n"
                    "- Use a conversational, engaging tone.\n"
                    "- Use bullet points, sections, or narrative as you see fit for clarity and impact.\n"
                    "- Reference file names, dates, or section headers where possible.\n"
                    "- Do not add information that is not present in the results, but you may offer thoughtful analysis, context, or commentary based on what is present.\n"
                    "- If the results are lengthy, provide a high-level summary first, then details.\n"
                    "- Your goal is to be genuinely helpful, insightful, and memorable—not just a calculator."
                )},
                {"role": "user", "content": f"User query: {user_prompt}\n\nSearch results:\n{top_text}"}
            ]
            summary = chat_completion(summary_prompt, model="gpt-5")
    except Exception:
        summary = None

    # Compact response support for connectors/function callers
    # Default to compact=True to avoid very large JSON responses (helps connectors/importers)
    compact = data.get("compact") if isinstance(data, dict) else None
    # If caller didn't send explicit compact, default True for assistant/webhook usage
    if compact is None:
        compact = True

    if compact:
        try:
            max_chunks = int(data.get("max_chunks", 3)) if isinstance(data, dict) and data.get("max_chunks") is not None else 3
        except Exception:
            max_chunks = 3
        try:
            excerpt_length = int(data.get("excerpt_length", 300)) if isinstance(data, dict) and data.get("excerpt_length") is not None else 300
        except Exception:
            excerpt_length = 300

        # Build compact sources list from filtered_chunks sorted by final_score
        sorted_filtered = sorted(filtered_chunks, key=lambda x: x.get("final_score", 0), reverse=True)
        sources = []
        for c in sorted_filtered[:max_chunks]:
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

        return JSONResponse({"summary": summary, "sources": sources})
    else:
        return JSONResponse({"retrieved_chunks": filtered_chunks, "summary": summary})


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
