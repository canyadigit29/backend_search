import json
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

        try:
            uuid.UUID(str(payload.user_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format. Must be a UUID.")

        # If previous_chunks are provided, this is a follow-up query. Use all previous chunks for LLM answer extraction.
        if payload.previous_chunks:
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

        # Embed the extracted query
        embedding_response = client.embeddings.create(
            model="text-embedding-3-large",
            input=extracted_query
        )
        embedding = embedding_response.data[0].embedding

        # Use the same hybrid/boosted search logic as /file_ops/search_docs
        from app.api.file_ops.search_docs import extract_entities_and_intent, embed_text, perform_search, keyword_search
        search_query = extracted_query
        query_info = extract_entities_and_intent(prompt)
        try:
            embedding = embed_text(search_query)
        except Exception as e:
            return {"error": f"Failed to generate embedding: {e}"}
        tool_args = {
            "embedding": embedding,
            "user_id_filter": payload.user_id,
            "file_name_filter": None,
            "description_filter": None,
            "start_date": None,
            "end_date": None,
            "user_prompt": prompt,
            "search_query": search_query  # Pass extracted search phrase for boosting
        }
        # --- Hybrid search: semantic + keyword ---
        # Only call perform_search, which now handles all boosting, merging, and debug output
        semantic_result = perform_search(tool_args)
        chunks = semantic_result.get("retrieved_chunks", [])

        # --- LLM-based summary of top search results (mirroring /file_ops/search_docs.py) ---
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
            if top_text.strip():
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
                    {"role": "user", "content": f"User query: {prompt}\n\nSearch results:\n{top_text}"}
                ]
                summary = chat_completion(summary_prompt, model="gpt-4o")
            else:
                pass
        except Exception as e:
            summary = None

        return {"retrieved_chunks": chunks, "summary": summary, "extracted_query": extracted_query}

    except Exception as e:
        return {"error": str(e)}
