import json
import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel

from app.api.file_ops.search_docs import perform_search
from app.core.openai_client import chat_completion
from app.core.supabase_client import supabase
from app.core.llm_answer_extraction import extract_answer_from_chunks_batched

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in environment")

client = OpenAI(api_key=openai_api_key)

class ChatRequest(BaseModel):
    user_prompt: str
    user_id: str
    session_id: str
    previous_chunks: list = None  # Optional, for follow-up queries

@router.post("/chat")
async def chat_with_context(payload: ChatRequest):
    try:
        prompt = payload.user_prompt.strip()
        logger.debug(f"🔍 User prompt: {prompt}")
        logger.debug(f"👤 User ID: {payload.user_id}")

        try:
            uuid.UUID(str(payload.user_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format. Must be a UUID.")

        # If previous_chunks are provided, this is a follow-up query. Use all previous chunks for LLM answer extraction.
        if payload.previous_chunks:
            import sys
            print(f"[DEBUG] Using previous_chunks for follow-up answer extraction: {len(payload.previous_chunks)} chunks", file=sys.stderr)
            sys.stderr.flush()
            # Use batching for large numbers of chunks
            answer = extract_answer_from_chunks_batched(prompt, [c.get("content", "") for c in payload.previous_chunks])
            return {"answer": answer, "used_chunks": len(payload.previous_chunks)}

        # LLM-based query extraction
        system_prompt = (
            "You are a helpful assistant. Extract the main topic, keywords, or search query from the user's request. "
            "Return only the search phrase or keywords, not instructions."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        extracted_query = chat_completion(messages)
        logger.debug(f"[LLM] Extracted search query: {extracted_query}")

        # Embed the extracted query
        embedding_response = client.embeddings.create(
            model="text-embedding-3-large",
            input=extracted_query
        )
        embedding = embedding_response.data[0].embedding

        # Use the same hybrid/boosted search logic as /file_ops/search_docs
        from app.api.file_ops.search_docs import extract_search_query, extract_entities_and_intent, embed_text, keyword_search
        # LLM-based query extraction (already done above as extracted_query)
        search_query = extracted_query
        query_info = extract_entities_and_intent(prompt)
        try:
            embedding = embed_text(search_query)
        except Exception as e:
            logger.error(f"❌ Failed to generate embedding: {e}")
            return {"error": f"Failed to generate embedding: {e}"}
        tool_args = {
            "embedding": embedding,
            "user_id_filter": payload.user_id,
            "file_name_filter": None,
            "description_filter": None,
            "start_date": None,
            "end_date": None,
            "user_prompt": prompt
        }
        # --- Hybrid search: semantic + keyword ---
        semantic_result = perform_search(tool_args)
        semantic_matches = semantic_result.get("retrieved_chunks", [])
        import re
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        keywords = [w for w in re.split(r"\W+", search_query) if w and w.lower() not in stopwords]
        keyword_results = keyword_search(keywords, user_id_filter=payload.user_id)
        all_matches = {m["id"]: m for m in semantic_matches}
        phrase = search_query.strip('"') if search_query.startswith('"') and search_query.endswith('"') else search_query
        phrase_lower = phrase.lower()
        boosted_ids = set()
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
        chunks = matches
        logger.debug(f"✅ Retrieved {len(chunks)} document chunks (hybrid/boosted logic).")

        # --- LLM-based summary of top search results (mirroring /file_ops/search_docs.py) ---
        import sys
        print(f"[DEBUG] Number of matches for summary: {len(chunks)}", file=sys.stderr)
        sys.stderr.flush()
        summary = None
        try:
            MAX_SUMMARY_CHARS = 60000
            sorted_chunks = sorted(chunks, key=lambda x: x.get("score", 0), reverse=True)
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
            print(f"[DEBUG] top_text length: {len(top_text)}", file=sys.stderr)
            print(f"[DEBUG] top_text preview: {top_text[:300]}...", file=sys.stderr)
            sys.stderr.flush()
            if top_text.strip():
                summary_prompt = [
                    {"role": "system", "content": "You are an expert assistant. Summarize the following retrieved search results for the user in a concise, clear, and helpful way. Only include information relevant to the user's query."},
                    {"role": "user", "content": f"User query: {prompt}\n\nSearch results:\n{top_text}"}
                ]
                print(f"[DEBUG] summary_prompt: {summary_prompt}", file=sys.stderr)
                sys.stderr.flush()
                summary = chat_completion(summary_prompt, model="gpt-4o")
                print(f"[DEBUG] Summary result: {summary[:300]}...", file=sys.stderr)
                sys.stderr.flush()
            else:
                print("[DEBUG] No top_text to summarize.", file=sys.stderr)
                sys.stderr.flush()
        except Exception as e:
            print(f"[DEBUG] Failed to generate summary: {e}", file=sys.stderr)
            sys.stderr.flush()
            summary = None

        return {"retrieved_chunks": chunks, "summary": summary, "extracted_query": extracted_query}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
