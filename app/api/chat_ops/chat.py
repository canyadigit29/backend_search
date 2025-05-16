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
CODE_ASSISTANT_ID = os.getenv("CODE_ASSISTANT_ID")
SEARCH_ASSISTANT_ID = os.getenv("SEARCH_ASSISTANT_ID")
HUB_ASSISTANT_ID = os.getenv("HUB_ASSISTANT_ID")

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

        save_message(
            user_id=payload.user_id,
            project_id=GENERAL_CONTEXT_PROJECT_ID,
            content=prompt,
            session_id=payload.session_id,
            speaker_role="user"
        )

        last_reply = (
            supabase.table("memory_log")
            .select("speaker_role")
            .eq("user_id", payload.user_id)
            .eq("session_id", payload.session_id)
            .neq("speaker_role", "user")
            .order("message_index", desc=True)
            .limit(1)
            .execute()
        )
        last_assistant = None
        if last_reply.data:
            last_assistant = last_reply.data[0]["speaker_role"]
        logger.debug(f"🤖 Last assistant speaker: {last_assistant}")

        memory_result = retrieve_memory({"query": prompt, "user_id": payload.user_id, "session_id": payload.session_id})
        context = "\n".join([m["content"] for m in memory_result.get("results", [])[:5]]) if memory_result.get("results") else None

        assistant_id = None
        lower_prompt = prompt.lower()
        if any(kw in lower_prompt for kw in ["code", "script", "function"]):
            assistant_id = CODE_ASSISTANT_ID
        elif any(kw in lower_prompt for kw in ["search", "scan", "find"]):
            assistant_id = SEARCH_ASSISTANT_ID
        elif last_assistant == "NerdGPT":
            assistant_id = CODE_ASSISTANT_ID
        elif last_assistant == "SearchGPT":
            assistant_id = SEARCH_ASSISTANT_ID
        else:
            assistant_id = HUB_ASSISTANT_ID

        thread = client.beta.threads.create()

        # 🧠 Inject document search context ONLY for SearchGPT
        if assistant_id == SEARCH_ASSISTANT_ID:
            embedding_response = client.embeddings.create(
                model="text-embedding-3-small",
                input=prompt
            )
            embedding = embedding_response.data[0].embedding
            doc_results = perform_search({"embedding": embedding})
            chunks = doc_results.get("results", [])

            if len(chunks) > 40:
                clarification_msg = "I found a large number of potentially relevant records. Could you help narrow it down — perhaps by specifying a year, topic, or department?"
                return {"answer": clarification_msg}

            # Feed in batches of 20 chunks
            batch_size = 20
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i + batch_size]
                joined_text = "\n\n".join(c["content"] for c in batch)
                client.beta.threads.messages.create(
                    thread_id=thread.id,
                    role="user",
                    content=f"Document context batch {i//batch_size + 1}:\n{joined_text}"
                )

            # Final prompt to generate response
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=f"Based on all the previous document context, answer the question: {prompt}"
            )
        else:
            # Non-search assistants get original prompt + memory context
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=(f"{prompt}\n\nRelevant context:\n{context}" if context else prompt)
            )

        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant_id,
        )

        while run.status in ["queued", "in_progress"]:
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

        messages = client.beta.threads.messages.list(thread_id=thread.id)
        reply = messages.data[0].content[0].text.value if messages.data else "(No reply)"

        # 🔁 Check for silent handoff signal
        if reply.strip() == "[handoff_to_hub]":
            logger.info("↩️ Assistant handed off back to hub")
            fallback_reply = "Just to make sure I understood — is this something related to code, documents, or something else?"
            return {"answer": fallback_reply}

        save_message(
            user_id=payload.user_id,
            project_id=GENERAL_CONTEXT_PROJECT_ID,
            content=reply,
            session_id=payload.session_id,
            speaker_role=(
                "NerdGPT" if assistant_id == CODE_ASSISTANT_ID else
                "SearchGPT" if assistant_id == SEARCH_ASSISTANT_ID else
                "HubGPT"
            )
        )

        return {"answer": reply}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
