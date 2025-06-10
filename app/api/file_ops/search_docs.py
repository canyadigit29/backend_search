import json
import os
from collections import defaultdict
import sys

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.openai_client import chat_completion
from app.core.query_understanding import extract_entities_and_intent

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import tiktoken



SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

def perform_search(tool_args):
    print("[DEBUG] perform_search called", flush=True)
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
            print("[DEBUG] perform_search early return: missing embedding and no text to embed", flush=True)
            return {"error": "Embedding must be provided to perform similarity search."}
        from app.api.file_ops.embed import embed_text
        query_embedding = embed_text(text_to_embed)

    if not user_id_filter:
        print("[DEBUG] perform_search early return: missing user_id", flush=True)
        return {"error": "user_id must be provided to perform search."}
    try:
        print("[DEBUG] perform_search main try block entered", flush=True)
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
        response = supabase.rpc("match_documents", rpc_args).execute()
        if getattr(response, "error", None):
            print(f"[DEBUG] perform_search Supabase RPC error: {response.error.message}", flush=True)
            return {"error": f"Supabase RPC failed: {response.error.message}"}
        semantic_matches = response.data or []
        print(f"[DEBUG] perform_search matches retrieved: {len(semantic_matches)}", flush=True)
        if semantic_matches:
            print(f"[DEBUG] perform_search first match sample: {str(semantic_matches[0])[:300]}", flush=True)
        else:
            print("[DEBUG] perform_search: matches is empty, returning early", flush=True)
        semantic_matches.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Debug: print top 5 semantic matches before any boosting
        print("[DEBUG] Top 5 semantic matches before boosting:", flush=True)
        for i, m in enumerate(semantic_matches[:5]):
            preview = (m.get("content", "") or "")[:200].replace("\n", " ")
            print(f"[DEBUG] SEMANTIC #{i+1} | score: {m.get('score', 0)} | id: {m.get('id')} | preview: {preview}", flush=True)

        # Hybrid/boosted merging and debug output
        import re
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        # Use search_query from user_prompt if available
        if not search_query and user_prompt:
            search_query = user_prompt
        keywords = [w for w in re.split(r"\W+", search_query or "") if w and w.lower() not in stopwords]
        from app.api.file_ops.search_docs import keyword_search
        keyword_results = keyword_search(keywords, user_id_filter=user_id_filter)
        print(f"[DEBUG] Keyword search returned {len(keyword_results)} results.", flush=True)
        print("[DEBUG] Entered hybrid search/boosting section", flush=True)
        all_matches = {m["id"]: m for m in semantic_matches}
        phrase = (search_query or "").strip('"') if (search_query or "").startswith('"') and (search_query or "").endswith('"') else (search_query or "")
        phrase_lower = phrase.lower()
        boosted_ids = set()
        print(f"[DEBUG] Boosting check: phrase_lower='{phrase_lower}'", flush=True)
        for k in keyword_results:
            content_lower = k.get("content", "").lower()
            print(f"[DEBUG] Checking keyword result id={k.get('id')} content_lower[:100]='{content_lower[:100]}'", flush=True)
            orig_score = k.get("score", 0)
            # Determine if we should apply a higher boost
            num_words = len((phrase_lower or '').split())
            high_boost = (num_words <= 4) or (len(keyword_results) <= 3)
            if phrase_lower in content_lower:
                print(f"[DEBUG] BOOSTED: phrase '{phrase_lower}' found in content for id={k.get('id')}", flush=True)
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
                    print(f"[DEBUG] BOOSTED: keyword overlap for id={k.get('id')}", flush=True)
                    all_matches[k["id"]]["original_score"] = prev_score
                    all_matches[k["id"]]["score"] = prev_score + 1.0  # Additive boost for keyword overlap
                    all_matches[k["id"]]["boosted_reason"] = "keyword_overlap"
                    boosted_ids.add(k["id"])
            else:
                print(f"[DEBUG] No boost for id={k.get('id')}", flush=True)
                k["score"] = orig_score + 0.5  # Additive, but still lower for pure keyword (was 0.8)
                all_matches[k["id"]] = k
        matches = list(all_matches.values())
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        # New debug: print top 5 results with score and content preview, and boosting info
        print("[DEBUG] Top 5 search results:", flush=True)
        for i, m in enumerate(matches[:5]):
            preview = (m.get("content", "") or "")[:200].replace("\n", " ")
            boost_info = ""
            if m.get("boosted_reason") == "exact_phrase":
                boost_info = f" [BOOSTED: exact phrase, orig_score={m.get('original_score', 'n/a')}]"
            elif m.get("boosted_reason") == "keyword_overlap":
                boost_info = f" [BOOSTED: keyword overlap, orig_score={m.get('original_score', 'n/a')}]"
            sim_score = m.get("score", 0)
            orig_score = m.get("original_score", sim_score)
            print(f"[DEBUG] #{i+1} | sim_score: {sim_score} | orig_score: {orig_score} | id: {m.get('id')} | preview: {preview}{boost_info}", flush=True)
        # After boosting, print all boosted results
        boosted_results = [m for m in matches if m.get("boosted_reason")]
        if boosted_results:
            print(f"[DEBUG] Boosted results found: {len(boosted_results)}", flush=True)
            for m in boosted_results:
                preview = (m.get("content", "") or "")[:200].replace("\n", " ")
                boost_info = f" [BOOSTED: {m.get('boosted_reason')}, orig_score={m.get('original_score', 'n/a')}]"
                sim_score = m.get("score", 0)
                orig_score = m.get("original_score", sim_score)
                print(f"[DEBUG] BOOSTED RESULT | sim_score: {sim_score} | orig_score: {orig_score} | id: {m.get('id')} | preview: {preview}{boost_info}", flush=True)
        else:
            print("[DEBUG] No boosted results found.", flush=True)
        print(f"[DEBUG] matches for response: {len(matches)}", flush=True)
        # Limit to top 20 results for all semantic/hybrid searches
        matches = matches[:20]
        print(f"[DEBUG] Returning {len(matches)} matches (max 20) in perform_search.", flush=True)
        return {"retrieved_chunks": matches}
    except Exception as e:
        print(f"[DEBUG] perform_search exception: {str(e)}", flush=True)
        return {"error": f"Error during search: {str(e)}"}


def extract_search_query(user_prompt: str) -> str:
    """
    Use OpenAI to extract the main topic or keywords for semantic search from the user prompt.
    """
    system_prompt = (
        "You are a helpful assistant. Extract the main topic, keywords, or search query from the user's request. "
        "Return only the search phrase or keywords, not instructions."
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
    Simple keyword search over document_chunks table. Returns chunks containing any of the keywords.
    """
    query = supabase.table("document_chunks").select("*")
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
    # Build OR filter for keywords (fix: use correct syntax)
    or_filters = [f"content.ilike.%{kw}%" for kw in keywords]
    if or_filters:
        query = query.or_(",".join(or_filters))
    query = query.limit(match_count)
    results = query.execute().data or []
    return results


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
    # --- Entity and intent extraction ---
    query_info = extract_entities_and_intent(user_prompt)
    try:
        embedding = embed_text(search_query)
    except Exception as e:
        return JSONResponse({"error": f"Failed to generate embedding: {e}"}, status_code=500)
    tool_args = {
        "embedding": embedding,
        "user_id_filter": user_id,
        "file_name_filter": data.get("file_name_filter"),
        "description_filter": data.get("description_filter"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "user_prompt": user_prompt,  # Pass original prompt for downstream use
        "search_query": search_query  # Pass extracted search phrase for boosting
    }
    # --- Semantic search only (no hybrid) ---
    semantic_result = perform_search(tool_args)
    matches = semantic_result.get("retrieved_chunks", [])
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
