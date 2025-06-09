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



    if not query_embedding:

        return {"error": "Embedding must be provided to perform similarity search."}
    if not user_id_filter:

        return {"error": "user_id must be provided to perform search."}
    try:
        try:
            total_chunks = supabase.table("document_chunks").select("id").execute().data
        except Exception as e:
            pass
        try:
            user_chunks = supabase.table("document_chunks").select("id").eq("user_id", user_id_filter).execute().data
        except Exception as e:
            pass
        try:
            with_embedding = supabase.table("document_chunks").select("id").not_.is_("openai_embedding", None).execute().data
        except Exception as e:
            pass
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": user_id_filter,
            "file_name_filter": file_name_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date,
            "match_threshold": 0.2,  # Lowered to 0.3 for more inclusive search
            "match_count": tool_args.get("match_count", 300)  # Increased default to 500, allow override
        }
        response = supabase.rpc("match_documents", rpc_args).execute()
        if getattr(response, "error", None):
            return {"error": f"Supabase RPC failed: {response.error.message}"}
        matches = response.data or []
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        # Remove old top match debug
        # if matches:
        #     top = matches[0]
        #     preview = top["content"][:200].replace("\n", " ")
        #     print(f"[DEBUG] Top match score: {top.get('score')}, preview: {preview}", file=sys.stderr)
        #     logger.debug(f"🔝 Top match (score {top.get('score')}): {preview}")
        # else:
        #     print("[DEBUG] No matches found", file=sys.stderr)
        #     logger.debug("⚠️ No matches found.")
        # Print top 5 results with logger.debug for Railway
        for i, m in enumerate(matches[:5]):
            preview = m.get("content", "")[:200].replace("\n", " ")
        if expected_phrase:
            expected_lower = expected_phrase.lower()
            matches = [x for x in matches if expected_lower not in x["content"].lower()]
        return {"retrieved_chunks": matches}
    except Exception as e:
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
    # Use the global supabase client instead of creating a new one
    query = supabase.table("document_chunks").select("*")  # <-- Fix: add .select("*")
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
    # Build OR filter for keywords
    or_filters = []
    for kw in keywords:
        or_filters.append(f"content.ilike.%{{kw}}%")
    if or_filters:
        query = query.or_(" , ".join(or_filters))
    query = query.limit(match_count)
    results = query.execute().data or []
    return results


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
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
        "user_prompt": user_prompt  # Pass original prompt for downstream use
    }
    # --- Hybrid search: semantic + keyword ---
    semantic_result = perform_search(tool_args)
    semantic_matches = semantic_result.get("retrieved_chunks", [])
    # Extract keywords from search_query (split on spaces, remove stopwords for simplicity)
    import re
    stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
    keywords = [w for w in re.split(r"\W+", search_query) if w and w.lower() not in stopwords]
    keyword_results = keyword_search(keywords, user_id_filter=user_id)
    # Merge results: boost or deduplicate
    all_matches = {m["id"]: m for m in semantic_matches}
    phrase = search_query.strip('"') if search_query.startswith('"') and search_query.endswith('"') else search_query
    phrase_lower = phrase.lower()
    boosted_ids = set()
    # For each keyword result, print if the phrase is in the content
    for k in keyword_results:
        content_lower = k.get("content", "").lower()
        orig_score = k.get("score", 0)
        if phrase_lower in content_lower:
            k["score"] = 1.2  # Strong boost for exact phrase match
            k["boosted_reason"] = "exact_phrase"
            k["original_score"] = orig_score
            all_matches[k["id"]] = k
            boosted_ids.add(k["id"])
        elif k["id"] in all_matches:
            prev_score = all_matches[k["id"]].get("score", 0)
            if prev_score < 1.0:
                all_matches[k["id"]]["original_score"] = prev_score
                all_matches[k["id"]]["score"] = 1.0  # Boost score
                all_matches[k["id"]]["boosted_reason"] = "keyword_overlap"
                boosted_ids.add(k["id"])
        else:
            k["score"] = 0.8  # Lower score for pure keyword
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
    print(f"[DEBUG] matches for response: {len(matches)}", flush=True)
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

    # --- LLM-based summary of top search results ---
    
    summary = None
    try:
        # GPT-4o supports a large context window; include as many top chunks as fit in ~60,000 chars
        MAX_SUMMARY_CHARS = 60000
        sorted_chunks = sorted(matches, key=lambda x: x.get("score", 0), reverse=True)
        top_texts = []
        total_chars = 0
        for chunk in sorted_chunks:
            content = chunk.get("content", "")
            if not content:
                continue
            if total_chars + len(content) > MAX_SUMMARY_CHARS:
                break
            top_texts.append(content)
            total_chars += len(content)
        top_text = "\n\n".join(top_texts)


        if top_text.strip():
            from app.core.openai_client import chat_completion
            summary_prompt = [
                {"role": "system", "content": (
                    "You are an expert assistant. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible.\n"
                    "- Focus on information directly relevant to the user's question.\n"
                    "- Group similar findings and highlight key points.\n"
                    "- Use bullet points or sections if it helps clarity.\n"
                    "- Reference file names, dates, or section headers where possible.\n"
                    "- Do not add information that is not present in the results.\n"
                    "- If the results are lengthy, provide a high-level summary first, then details."
                )},
                {"role": "user", "content": f"User query: {user_prompt}\n\nSearch results:\n{top_text}"}
            ]
            summary = chat_completion(summary_prompt, model="gpt-4o")
        else:
            pass
    except Exception as e:
        pass
        summary = None

    return JSONResponse({"retrieved_chunks": retrieved_chunks, "summary": summary})


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
