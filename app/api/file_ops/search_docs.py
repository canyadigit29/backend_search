import json
import os
from collections import defaultdict
import sys
import statistics
import httpx
import time
import uuid

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.openai_client import chat_completion
from app.core.query_understanding import extract_search_filters
from app.core.llama_query_transform import llama_query_transform

from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
import tiktoken

# Redis / RQ are optional. If unavailable, async job flow is disabled and we fallback to sync.
REDIS_URL = os.environ.get("REDIS_URL", "")
async_enabled = False
redis_client = None
rq_queue = None

try:
    import redis
    from rq import Queue

    if REDIS_URL:
        try:
            redis_client = redis.from_url(REDIS_URL)
            # Quick ping to ensure it's reachable
            try:
                redis_client.ping()
                rq_queue = Queue("search", connection=redis_client)
                async_enabled = True
            except Exception:
                # Redis is present but not reachable; disable async
                redis_client = None
                rq_queue = None
                async_enabled = False
        except Exception:
            redis_client = None
            rq_queue = None
            async_enabled = False
    else:
        # No REDIS_URL configured, leave async disabled
        redis_client = None
        rq_queue = None
        async_enabled = False
except Exception:
    # redis or rq import failed; continue with async disabled
    redis_client = None
    rq_queue = None
    async_enabled = False

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

# Configurable model/token settings (can be overridden via env)
MODEL_MAX_TOKENS = int(os.environ.get("MODEL_MAX_TOKENS", "400000"))
SUMMARY_RESPONSE_RESERVE = int(os.environ.get("SUMMARY_RESPONSE_RESERVE", "8192"))
CHUNK_TOKEN_SAFETY_MARGIN = int(os.environ.get("CHUNK_TOKEN_SAFETY_MARGIN", "512"))
# Cap how many chunk tokens we will consider (optional safety)
CHUNK_TOKEN_CAP = int(os.environ.get("CHUNK_TOKEN_CAP", str(300_000)))  # large default, still bounded by model limits
# Request timeout we need to respect from caller (59s typical)
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "59"))
INTERNAL_TIMEOUT_BUFFER = int(os.environ.get("INTERNAL_TIMEOUT_BUFFER", "4"))

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
        semantic_matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Hybrid/boosted merging
        import re
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        if not search_query and user_prompt:
            search_query = user_prompt
        keywords = [w for w in re.split(r"\W+", search_query or "") if w and w.lower() not in stopwords]
        from app.api.file_ops.search_docs import keyword_search
        keyword_results = keyword_search(keywords, user_id_filter=user_id_filter)
        all_matches = {m["id"]: m for m in semantic_matches}
        for k in keyword_results:
            if k["id"] not in all_matches:
                all_matches[k["id"]] = k
        matches = list(all_matches.values())
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Filter matches by metadata
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

        # Scoring and normalization (unchanged)
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

        # Limit to top 100
        matches = matches[:100]

        # Keyword scores
        def count_keyword_hits(text, keywords):
            text_lower = text.lower()
            return sum(1 for kw in keywords if kw.lower() in text_lower)
        max_keyword_hits = 1
        for m in all_matches.values():
            hits = count_keyword_hits(m.get("content", ""), keywords)
            m["keyword_score"] = hits
            if hits > max_keyword_hits:
                max_keyword_hits = hits
        for m in all_matches.values():
            m["keyword_score"] = m["keyword_score"] / max_keyword_hits if max_keyword_hits > 0 else 0

        # Normalize semantic score
        semantic_scores = [m.get("score", 0) for m in all_matches.values()]
        min_sem = min(semantic_scores) if semantic_scores else 0
        max_sem = max(semantic_scores) if semantic_scores else 1
        for m in all_matches.values():
            raw = m.get("score", 0)
            m["semantic_score"] = (raw - min_sem) / (max_sem - min_sem) if max_sem > min_sem else 0

        # Weighted sum
        alpha = 0.6
        beta = 0.4
        for m in all_matches.values():
            m["final_score"] = alpha * m["semantic_score"] + beta * m["keyword_score"]

        matches = list(all_matches.values())
        threshold = 0.4
        matches = [m for m in matches if m["final_score"] >= threshold]
        matches.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        return {"retrieved_chunks": matches}
    except Exception as e:
        return {"error": f"Error during search: {str(e)}"}


def extract_search_query(user_prompt: str, agent_mode: bool = False) -> str:
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
        response = httpx.post(endpoint, headers=headers, json=rpc_args, timeout=10)
        response.raise_for_status()
        results = response.json() or []
        for r in results:
            r["keyword_score"] = r.get("ts_rank", 0)
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
async def api_search_docs(request: Request, background_tasks: BackgroundTasks):
    start_time = time.monotonic()
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
        "search_query": search_query
    }
    for meta_field in [
        "document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title", "file_extension", "section_header", "page_number"
    ]:
        if search_filters.get(meta_field) is not None:
            tool_args[meta_field] = search_filters[meta_field]

    semantic_result = perform_search(tool_args)
    matches = semantic_result.get("retrieved_chunks", [])

    # attach neighbors
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

    # Decide sync vs async: if close to timeout, enqueue
    elapsed = time.monotonic() - start_time
    remaining = REQUEST_TIMEOUT_SECONDS - INTERNAL_TIMEOUT_BUFFER - elapsed
    # allow caller to force async
    force_async = data.get("async") or data.get("background")

    if (force_async or remaining < 3.0) and async_enabled:
        job_id = str(uuid.uuid4())
        # enqueue worker (process_search_job defined in app.tasks.search_worker)
        rq_queue.enqueue("app.tasks.search_worker.process_search_job", tool_args, job_id, data.get("callback_url"))
        redis_client.set(f"search:results:{job_id}", json.dumps({"status": "queued"}))
        return JSONResponse({"status": "queued", "job_id": job_id, "poll_url": f"/file_ops/jobs/{job_id}"}, status_code=202)

    # If async was requested but is unavailable, fall back to synchronous summarization.
    # else, perform summarization inline (existing behavior)
    summary = None
    filtered_chunks = []
    try:
        system_prompt_text = (
            "You are an insightful, engaging, and helpful assistant. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible.\n"
            "- Focus on information directly relevant to the user's question. Synthesize and connect the dots only from the provided results.\n"
            "- Use bullet points, sections, or narrative as needed for clarity.\n"
            "- Reference file names, dates, or section headers where possible.\n"
            "- Do not add information not present in the results.\n"
        )
        user_wrapper_text = f"User query: {user_prompt}\n\nSearch results:\n"
        try:
            encoding = tiktoken.encoding_for_model("gpt-5")
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")

        def build_summary_text_from_chunks(sorted_chunks, system_prompt_text, user_wrapper_text, model="gpt-5"):
            model_max = MODEL_MAX_TOKENS
            reserve = SUMMARY_RESPONSE_RESERVE
            safety = CHUNK_TOKEN_SAFETY_MARGIN
            overhead_tokens = len(encoding.encode(system_prompt_text or "")) + len(encoding.encode(user_wrapper_text or ""))
            available_for_chunks = max(0, model_max - reserve - overhead_tokens - safety)
            available_for_chunks = min(available_for_chunks, CHUNK_TOKEN_CAP)
            top_texts = []
            total_tokens = 0
            used_chunk_ids = set()
            for chunk in sorted_chunks:
                content = chunk.get("content", "")
                if not content:
                    continue
                chunk_tokens = len(encoding.encode(content))
                if total_tokens + chunk_tokens > available_for_chunks:
                    break
                top_texts.append(content)
                total_tokens += chunk_tokens
                used_chunk_ids.add(chunk.get("id"))
            top_text = "\n\n".join(top_texts)
            return top_text, used_chunk_ids

        sorted_chunks = sorted(matches, key=lambda x: x.get("score", 0), reverse=True)
        top_text, used_chunk_ids = build_summary_text_from_chunks(sorted_chunks, system_prompt_text, user_wrapper_text, model="gpt-5")

        # Filter matches to only those used in the summary
        filtered_chunks = [chunk for chunk in sorted_chunks if chunk.get("id") in used_chunk_ids]
        print(f"[DEBUG] Returning {len(filtered_chunks)} chunks actually used in summary (token-budgeted).", flush=True)

        # Limit to top 50 sources for frontend
        filtered_chunks = filtered_chunks[:50]
        print(f"[DEBUG] Returning {len(filtered_chunks)} chunks (max 50) actually used in summary.", flush=True)

        if top_text.strip():
            from app.core.openai_client import chat_completion
            summary_prompt = [
                {"role": "system", "content": system_prompt_text},
                {"role": "user", "content": f"{user_wrapper_text}{top_text}"}
            ]
            # Use configured reserve via SUMMARY_RESPONSE_RESERVE when calling the model if chat_completion supports max_tokens.
            try:
                summary = chat_completion(summary_prompt, model="gpt-5")
                if isinstance(summary, str):
                    summary = summary.strip()
            except TypeError:
                # Fallback in case chat_completion signature differs
                summary = chat_completion(summary_prompt)
                if isinstance(summary, str):
                    summary = summary.strip()
        else:
            pass
    except Exception as e:
        pass
        summary = None

    return JSONResponse({"retrieved_chunks": filtered_chunks, "summary": summary})

@router.get("/file_ops/jobs/{job_id}")
async def get_job_status(job_id: str):
    if not async_enabled:
        return JSONResponse({"status": "not_configured", "error": "Async jobs are not configured on this instance"}, status_code=503)
    key = f"search:results:{job_id}"
    data = redis_client.get(key)
    if not data:
        return JSONResponse({"status": "not_found"}, status_code=404)
    try:
        payload = json.loads(data)
    except Exception:
        payload = {"status": "unknown", "raw": data.decode() if isinstance(data, bytes) else str(data)}
    return JSONResponse(payload)

@router.post("/assistant/search_docs")
async def assistant_search_docs(request: Request):
    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON payload: {e}"}, status_code=400)

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
        "search_query": search_query
    }
    for meta_field in [
        "document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title", "file_extension", "section_header", "page_number"
    ]:
        if search_filters.get(meta_field) is not None:
            tool_args[meta_field] = search_filters[meta_field]

    # forward to perform_search and reuse the same enqueue decision as above
    start_time = time.monotonic()
    semantic_result = perform_search(tool_args)
    matches = semantic_result.get("retrieved_chunks", [])
    # attach neighbors
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

    matches = postprocess_chunks(matches, expand_neighbors=True, deduplicate=True)

    elapsed = time.monotonic() - start_time
    remaining = REQUEST_TIMEOUT_SECONDS - INTERNAL_TIMEOUT_BUFFER - elapsed
    force_async = data.get("async") or data.get("background")
    if (force_async or remaining < 3.0) and async_enabled:
        job_id = str(uuid.uuid4())
        rq_queue.enqueue("app.tasks.search_worker.process_search_job", tool_args, job_id, data.get("callback_url"))
        redis_client.set(f"search:results:{job_id}", json.dumps({"status": "queued"}))
        return JSONResponse({"status": "queued", "job_id": job_id, "poll_url": f"/file_ops/jobs/{job_id}"}, status_code=202)

    # otherwise reuse summarization flow (copied from above)
    summary = None
    try:
        try:
            encoding = tiktoken.encoding_for_model("gpt-5")
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        system_prompt_text = (
            "You are an insightful, engaging, and helpful assistant. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible.\n"
            "- Focus on information directly relevant to the user's question. Synthesize and connect the dots only from the provided results.\n"
            "- Use bullet points, sections, or narrative as needed for clarity.\n"
            "- Reference file names, dates, or section headers where possible.\n"
            "- Do not add information not present in the results.\n"
        )
        user_wrapper_text = f"User query: {user_prompt}\n\nSearch results:\n"

        def build_summary_text_from_chunks(sorted_chunks, system_prompt_text, user_wrapper_text, model="gpt-5"):
            model_max = MODEL_MAX_TOKENS
            reserve = SUMMARY_RESPONSE_RESERVE
            safety = CHUNK_TOKEN_SAFETY_MARGIN
            overhead_tokens = len(encoding.encode(system_prompt_text or "")) + len(encoding.encode(user_wrapper_text or ""))
            available_for_chunks = max(0, model_max - reserve - overhead_tokens - safety)
            available_for_chunks = min(available_for_chunks, CHUNK_TOKEN_CAP)
            top_texts = []
            total_tokens = 0
            used_chunk_ids = set()
            for chunk in sorted_chunks:
                content = chunk.get("content", "")
                if not content:
                    continue
                chunk_tokens = len(encoding.encode(content))
                if total_tokens + chunk_tokens > available_for_chunks:
                    break
                top_texts.append(content)
                total_tokens += chunk_tokens
                used_chunk_ids.add(chunk.get("id"))
            top_text = "\n\n".join(top_texts)
            return top_text, used_chunk_ids

            sorted_chunks = sorted(matches, key=lambda x: x.get("score", 0), reverse=True)
            top_text, used_chunk_ids = build_summary_text_from_chunks(sorted_chunks, system_prompt_text, user_wrapper_text, model="gpt-5")
            filtered_chunks = [chunk for chunk in sorted_chunks if chunk.get("id") in used_chunk_ids]
            filtered_chunks = filtered_chunks[:20]
            if top_text.strip():
                from app.core.openai_client import chat_completion
                summary_prompt = [
                    {"role": "system", "content": system_prompt_text},
                    {"role": "user", "content": f"{user_wrapper_text}{top_text}"}
                ]
                try:
                    summary = chat_completion(summary_prompt, model="gpt-5")
                    if isinstance(summary, str):
                        summary = summary.strip()
                except TypeError:
                    summary = chat_completion(summary_prompt)
                    if isinstance(summary, str):
                        summary = summary.strip()
    except Exception:
        summary = None

    compact = data.get("compact") if isinstance(data, dict) else None
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

@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
