import json
import os
from collections import defaultdict
import sys
import statistics

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.openai_client import chat_completion
from app.core.query_understanding import extract_search_filters

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
            "match_threshold": 0.3,  # Lowered from 3.5 to 0.3
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
        # Removed debug print: [DEBUG] Keyword search returned ...
        # Removed debug print: [DEBUG] Entered hybrid search/boosting section
        all_matches = {m["id"]: m for m in semantic_matches}
        phrase = (search_query or "").strip('"') if (search_query or "").startswith('"') and (search_query or "").endswith('"') else (search_query or "")
        phrase_lower = phrase.lower()
        boosted_ids = set()
        # Removed debug print: [DEBUG] Boosting check: phrase_lower=...
        for k in keyword_results:
            content_lower = k.get("content", "").lower()
            orig_score = k.get("score", 0)
            # Determine if we should apply a higher boost
            num_words = len((phrase_lower or '').split())
            high_boost = (num_words <= 4) or (len(keyword_results) <= 3)
            if phrase_lower in content_lower:
                if high_boost:
                    k["score"] = orig_score + 1.0  # Reduced boost for exact phrase match
                else:
                    k["score"] = orig_score + 0.08  # Default (legacy) boost
                k["boosted_reason"] = "exact_phrase"
                k["original_score"] = orig_score
                all_matches[k["id"]] = k
                boosted_ids.add(k["id"])
            elif k["id"] in all_matches:
                prev_score = all_matches[k["id"]].get("score", 0)
                if prev_score < 1.0:
                    all_matches[k["id"]]["original_score"] = prev_score
                    all_matches[k["id"]]["score"] = prev_score + 1.0  # Additive boost for keyword overlap
                    all_matches[k["id"]]["boosted_reason"] = "keyword_overlap"
                    boosted_ids.add(k["id"])
            else:
                k["score"] = orig_score + 0.5  # Additive, but still lower for pure keyword (was 0.8)
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
        # Limit to top 20 results for all semantic/hybrid searches
        matches = matches[:20]
        # Removed debug print: [DEBUG] Returning ... matches (max 20) in perform_search.
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
        alpha = 0.7  # weight for semantic
        beta = 0.3   # weight for keyword
        for m in all_matches.values():
            m["final_score"] = alpha * m["semantic_score"] + beta * m["keyword_score"]

        matches = list(all_matches.values())
        matches.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        return {"retrieved_chunks": matches}
    except Exception as e:
        # Removed debug print: [DEBUG] perform_search exception: ...
        return {"error": f"Error during search: {str(e)}"}


def extract_search_query(user_prompt: str) -> str:
    """
    Use OpenAI to extract the most effective search phrase or keywords for semantic document retrieval from the user's request.
    """
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
    # Build the FTS query string
    ts_query = ' & '.join([kw for kw in keywords if kw])
    query = supabase.table("document_chunks").select("*", "ts_rank:ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', %s))" % (" | ".join(keywords)))
    if user_id_filter:
        query = query.eq("user_id", user_id_filter)
    if file_name_filter:
        query = query.eq("file_name", file_name_filter)
    if description_filter:
        query = query.eq("description", description_filter)
    if start_date:
        query = query.gte("created_at", start_date)
    if end_date:
        query = query.lte("created_at", end_date)
    query = query.order("ts_rank", desc=True)
    query = query.limit(match_count)
    results = query.execute().data or []
    # Attach the ts_rank as keyword_score for downstream use
    for r in results:
        r["keyword_score"] = r.get("ts_rank", 0)
    return results


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
    # LLM-based query extraction
    search_query = extract_search_query(user_prompt)
    # --- Metadata filter extraction ---
    search_filters = extract_search_filters(user_prompt)
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
        "user_prompt": user_prompt,  # Pass original prompt for downstream use
        "search_query": search_query  # Pass extracted search phrase for boosting
    }
    # Add new metadata filters if present
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
    # --- LLM-based summary of top search results ---
    summary = None
    filtered_chunks = []
    try:
        # GPT-4o supports a large context window; include as many top chunks as fit in ~60,000 chars
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
        filtered_chunks = filtered_chunks[:20]
        print(f"[DEBUG] Returning {len(filtered_chunks)} chunks (max 20) actually used in summary.", flush=True)

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
            summary = chat_completion(summary_prompt, model="gpt-4o")
        else:
            pass
    except Exception as e:
        pass
        summary = None

    return JSONResponse({"retrieved_chunks": filtered_chunks, "summary": summary})


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
