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

        # Instead of calling perform_search directly, call the /file_ops/search_docs endpoint for hybrid/boosted search
        import requests
        backend_url = os.getenv("BACKEND_SEARCH_URL") or "http://localhost:8000"
        search_endpoint = f"{backend_url}/file_ops/search_docs"
        payload_data = {
            "user_prompt": prompt,
            "user_id": payload.user_id,
            "session_id": payload.session_id
        }
        try:
            resp = requests.post(search_endpoint, json=payload_data, timeout=60)
            resp.raise_for_status()
            doc_results = resp.json()
            chunks = doc_results.get("retrieved_chunks") or []
            logger.debug(f"✅ Retrieved {len(chunks)} document chunks (via /file_ops/search_docs endpoint).")
        except Exception as e:
            logger.error(f"❌ Error calling /file_ops/search_docs: {e}")
            return {"error": f"Failed to retrieve search results: {e}"}

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
