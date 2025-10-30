import os
import asyncio
import json
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

# Consolidate all necessary imports here
from app.core.openai_client import chat_completion
from app.core.config import settings
from app.core.logger import log_info, log_error
from app.api.file_ops.search_docs import assistant_search_docs

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Pydantic Models for Request and Response ---
class RagSearchRequest(BaseModel):
    query: str = Field(..., description="The user's search query.")
    # Allow other potential fields from the frontend to be ignored
    class Config:
        extra = 'ignore'

def plan_search_query(user_prompt: str) -> dict:
    """
    Uses an LLM to decompose a user query into a structured search plan.
    This is the "planner" step in the RAG pipeline.
    """
    # This prompt is now centralized here, at the entry point of the RAG system.
    system_prompt = (
        "You are a search query planner. Your task is to analyze the user's request and convert it into a structured JSON object representing the search logic. "
        "The output must be a single JSON object with two keys: 'operator' (which can be 'AND' or 'OR') and 'terms' (a list of strings). "
        "If there is only one logical concept, use the 'AND' operator with a single term in the list. "
        "Prioritize breaking down queries with explicit 'OR' clauses. "
        "Do not include conversational phrases or explanations in the terms. "
        "Do not use web search syntax like `site:` or `filetype:`."
        "\n\nExamples:\n"
        'User: find documents about "Mennonite Publishing House" OR "Herald Press"\n'
        'JSON: {"operator": "OR", "terms": ["Mennonite Publishing House", "Herald Press"]}\n\n'
        'User: what are the rules for zoning and code enforcement in the historic district\n'
        'JSON: {"operator": "AND", "terms": ["zoning rules historic district", "code enforcement historic district"]}\n\n'
        'User: reports on infrastructure spending in 2022\n'
        'JSON: {"operator": "AND", "terms": ["infrastructure spending 2022"]}\n'
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        result = chat_completion(messages)
        plan = json.loads(result)
        if isinstance(plan, dict) and "operator" in plan and "terms" in plan:
            return plan
    except (json.JSONDecodeError, TypeError):
        # Fallback for non-JSON responses or errors
        pass
    
    # Default fallback plan if LLM fails to produce valid JSON
    return {"operator": "AND", "terms": [user_prompt]}


# --- Main RAG Search Endpoint ---
@router.post("/rag-search")
async def rag_search(req: RagSearchRequest):
    """
    This is the consolidated RAG endpoint. It takes a user query,
    plans the search, executes it, and returns a synthesized response.
    """
    try:
        log_info(logger, "rag_search.request", {"query_len": len(req.query or "")})

        # 1. Plan the search based on the user query
        user_prompt = req.query
        search_plan = plan_search_query(user_prompt)

        # 2. Prepare the payload for the search executor
        # We pass the original request dict and add the search plan to it
        payload = req.dict()
        payload["user_prompt"] = user_prompt
        payload["search_plan"] = search_plan

        # 3. Execute the search by calling the decoupled search function
        result = await assistant_search_docs(payload)
        log_info(logger, "rag_search.completed", {})
        return result

    except Exception as e:
        # Log the full error for debugging
        log_error(logger, "rag_search.error", {"error": str(e)}, exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred in RAG pipeline: {str(e)}")
