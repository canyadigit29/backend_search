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
from ..file_ops.search_docs import assistant_search_docs

router = APIRouter()

# --- Pydantic Models for Request and Response ---
class RagSearchRequest(BaseModel):
    query: str = Field(..., description="The user's search query.")
    query_embedding: Optional[List[float]] = Field(None, description="Optional pre-computed query embedding.")
    response_mode: str = Field("summary", description="Response mode: 'summary' or 'structured_results'.")
    match_threshold: float = Field(0.6, description="Similarity threshold for matching.")
    match_count: int = Field(100, description="Number of matches to return.")
    file_ids: Optional[List[str]] = Field(None, description="Optional list of file IDs to search within.")
    file_name: Optional[str] = Field(None, description="Filter by file name.")
    description: Optional[str] = Field(None, description="Filter by description.")
    document_type: Optional[str] = Field(None, description="Filter by document type.")
    meeting_year: Optional[int] = Field(None, description="Filter by meeting year.")
    meeting_month: Optional[int] = Field(None, description="Filter by meeting month.")
    meeting_month_name: Optional[str] = Field(None, description="Filter by meeting month name.")
    meeting_day: Optional[int] = Field(None, description="Filter by meeting day.")
    ordinance_title: Optional[str] = Field(None, description="Filter by ordinance title.")

    class Config:
        extra = 'ignore' # Change to ignore to prevent unexpected fields

class RagSearchResponse(BaseModel):
    summary: Optional[str]
    sources: List[Dict]
    retrieved_chunks: Optional[List[Dict]] = None

# --- Main RAG Search Endpoint ---
@router.post("/rag-search")
async def rag_search(request: Request):
    """
    This is the consolidated RAG endpoint. It takes a user query,
    performs a semantic search, and returns a synthesized response.
    """
    # The request body is read once for logging.
    # The `assistant_search_docs` function will read it again from the request object.
    request_json = await request.json()
    print(f"--- RAG SEARCH REQUEST RECEIVED ---\n{request_json}\n---------------------------------")
    
    try:
        # The logic is now deferred to the more capable assistant_search_docs function.
        # We pass the entire request object to it.
        return await assistant_search_docs(request)
        
    except Exception as e:
        # Log the full error for debugging
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
