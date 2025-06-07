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

        # Generate a unique search_id for this search
        search_id = str(uuid.uuid4())

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

        # Perform semantic search
        doc_results = perform_search({
            "embedding": embedding,
            "user_id_filter": payload.user_id
        })
        chunks = doc_results.get("results") or doc_results.get("retrieved_chunks") or []
        logger.debug(f"✅ Retrieved {len(chunks)} document chunks.")

        # Attach search_id to each chunk for traceability (optional, for frontend convenience)
        for chunk in chunks:
            chunk["search_id"] = search_id

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

        # Return search_id in the response for robust follow-up Q&A
        return {"retrieved_chunks": chunks, "summary": summary, "extracted_query": extracted_query, "search_id": search_id}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
