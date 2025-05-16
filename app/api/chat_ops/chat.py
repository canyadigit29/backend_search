import json
import logging
import os
import uuid
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel

from app.api.file_ops.ingest import process_file
from app.api.memory_ops.session_memory import retrieve_memory, save_message
from app.api.file_ops.search_docs import perform_search
from app.api.file_ops.ingestion_worker import run_ingestion_once
from app.core.supabase_client import supabase

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

client = OpenAI()

GENERAL_CONTEXT_PROJECT_ID = "00000000-0000-0000-0000-000000000000"

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
            raise HTTPException(
                status_code=400, detail="Invalid user_id format. Must be a UUID."
            )

        # Save user prompt to memory
        save_message(
            user_id=payload.user_id,
            project_id=GENERAL_CONTEXT_PROJECT_ID,
            content=prompt,
            session_id=payload.session_id,
            speaker_role="user"
        )

        # Retrieve session context
        session_response = (
            supabase.table("session_logs")
            .select("speaker_role, content")
            .eq("user_id", payload.user_id)
            .order("message_index", desc=True)
            .limit(10)
            .execute()
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Max, a helpful assistant. You can answer general questions, search public internet sources using 'search_web', or analyze the user’s uploaded documents using 'search_docs'. Use 'search_web' only if the user explicitly asks to search the internet, online sources, or the web. Use 'search_docs' only if the user asks you to scan, find, analyze, or summarize something in their documents. If the user simply asks a question without referencing a source, answer it directly."
                ),
            }
        ]

        if session_response.data:
            past_messages = reversed(session_response.data)
            for row in past_messages:
                if row["speaker_role"] in ("user", "assistant"):
                    messages.append({"role": row["speaker_role"], "content": row["content"]})

        # Inject memory
        memory_result = retrieve_memory({"query": prompt, "user_id": payload.user_id, "session_id": payload.session_id})
        if memory_result.get("results"):
            memory_snippets = "\n".join([m["content"] for m in memory_result["results"][:3]])
            messages.insert(1, {
                "role": "system",
                "content": f"Relevant past memory:\n{memory_snippets}",
            })

        # Embed and search
        embedding_response = client.embeddings.create(
            model="text-embedding-3-small",
            input=prompt
        )
        embedding = embedding_response.data[0].embedding

        doc_results = perform_search({"embedding": embedding})
        summaries = []
        for chunk in doc_results.get("results", [])[:10]:
            summary_prompt = f"Summarize the following document content in 1–2 sentences:\n\n{chunk['content']}"
            summary_response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": summary_prompt}]
            )
            summaries.append(summary_response.choices[0].message.content)

        if summaries:
            summary_block = "\n\n".join(summaries)
            messages.insert(1, {
                "role": "system",
                "content": f"Summary of document excerpts about '{payload.user_prompt}':\n{summary_block}"
            })

        messages.append({"role": "user", "content": prompt})

        # Generate assistant reply
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tool_choice="auto"
        )
        logger.debug(f"🧪 OpenAI raw response: {response}")

        reply = response.choices[0].message.content if response.choices else "(No reply)"

        # Save assistant reply
        save_message(
            user_id=payload.user_id,
            project_id=GENERAL_CONTEXT_PROJECT_ID,
            content=reply,
            session_id=payload.session_id,
            speaker_role="HubGPT"
        )

        return {"answer": reply}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
