import os
import asyncio
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

# Consolidate all necessary imports here
from app.api.file_ops.embed import embed_text
from app.core.supabase_client import create_client
from app.core.openai_client import stream_chat_completion
from app.core.token_utils import trim_texts_to_token_limit

# Local modules for search logic
from ..file_ops.search_docs import perform_search

router = APIRouter()

# --- Pydantic Models for Request and Response ---
class RagSearchRequest(BaseModel):
    query: str = Field(..., description="The user's search query.")
    response_mode: str = Field("summary", description="Response mode: 'summary' or 'structured_results'.")
    file_ids: Optional[List[str]] = Field(None, description="Optional list of file IDs to search within.")
    file_name: Optional[str] = Field(None, description="Filter by file name.")
    description: Optional[str] = Field(None, description="Filter by description.")
    document_type: Optional[str] = Field(None, description="Filter by document type.")
    meeting_year: Optional[int] = Field(None, description="Filter by meeting year.")
    meeting_month: Optional[int] = Field(None, description="Filter by meeting month.")
    meeting_month_name: Optional[str] = Field(None, description="Filter by meeting month name.")
    meeting_day: Optional[int] = Field(None, description="Filter by meeting day.")
    ordinance_title: Optional[str] = Field(None, description="Filter by ordinance title.")
    # Allow other potential fields from the tool definition to be passed through
    class Config:
        extra = 'allow'

class RagSearchResponse(BaseModel):
    summary: Optional[str]
    sources: List[Dict]
    retrieved_chunks: Optional[List[Dict]] = None

# --- Main RAG Search Endpoint ---
@router.post("/rag-search", response_model=RagSearchResponse)
async def rag_search(request: RagSearchRequest):
    """
    This is the consolidated RAG endpoint. It takes a user query,
    performs a semantic search, and returns a synthesized response.
    """
    print(f"--- RAG SEARCH REQUEST RECEIVED ---\n{request.dict()}\n---------------------------------")
    user_prompt = request.query
    if not user_prompt:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    try:
        # 1. Generate Embedding for the main query
        try:
            embedding = embed_text(user_prompt)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate embedding: {e}")

        # 2. Perform Semantic Search
        # This logic is simplified to call the updated perform_search function
        tool_args = request.dict()
        tool_args["embedding"] = embedding
        
        search_result = perform_search(tool_args)
        matches = search_result.get("retrieved_chunks", [])

        # 3. Prepare for Summarization
        summary = None
        summary_was_partial = False # This is no longer relevant but kept for model compatibility

        if request.response_mode == "summary" and matches:
            # Build context for summarization
            annotated_texts = []
            for idx, chunk in enumerate(matches, start=1):
                header = f"[#{idx} id={chunk.get('id')} file_id={chunk.get('file_id')}]"
                body = (chunk.get("content", "") or "")[:3000] # Truncate
                annotated_texts.append(f"{header}\n{body}")
            
            top_text = trim_texts_to_token_limit(annotated_texts, 12000, model="gpt-4-turbo-preview")

            if top_text:
                summary_prompt_messages = [
                    {"role": "system", "content": "You are an insightful research assistant. Read the provided document chunks and produce a concise, accurate synthesis that directly answers the user's query. Cite evidence using the chunk ids (id=...)."},
                    {"role": "user", "content": f"User query: {user_prompt}\n\nSearch results:\n{top_text}\n\nPlease provide a detailed summary based on these results."}
                ]
                # Note: stream_chat_completion might need adjustment if it expects partial results, which we've removed.
                # For now, we assume it returns a full summary.
                summary_content, _ = stream_chat_completion(summary_prompt_messages, model="gpt-4-turbo-preview", max_tokens=4096)
                summary = summary_content

        # 4. Format Sources
        sources = []
        for c in matches:
            sources.append({
                "id": c.get("id"),
                "file_id": c.get("file_id"),
                "file_name": c.get("file_name"),
                "score": c.get("similarity"),
                "excerpt": (c.get("content", "") or "").strip().replace("\n", " ")[:300]
            })

        # 5. Construct Final Response
        response_data = {
            "summary": summary,
            "sources": sources,
        }

        if request.response_mode == "structured_results":
            response_data["retrieved_chunks"] = matches

        return response_data

    except Exception as e:
        # Log the full error for debugging
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
