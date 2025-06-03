import json
import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel

from app.api.file_ops.search_docs import perform_search
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

        # Embed the user prompt
        embedding_response = client.embeddings.create(
            model="text-embedding-3-large",
            input=prompt
        )
        embedding = embedding_response.data[0].embedding

        # Perform semantic search
        doc_results = perform_search({
            "embedding": embedding,
            "user_id_filter": payload.user_id
        })
        chunks = doc_results.get("results") or doc_results.get("retrieved_chunks") or []
        logger.debug(f"✅ Retrieved {len(chunks)} document chunks.")

        return {"retrieved_chunks": chunks}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
