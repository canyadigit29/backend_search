import json
import logging
import os
import uuid
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel
import tiktoken

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

        assistant_id = HUB_ASSISTANT_ID

        thread = client.beta.threads.create()

        def extract_core_search_phrase(prompt: str) -> str:
            junk_phrases = [
                "hi max", "can you", "please", "could you", "would you", "i was wondering",
                "thanks", "thank you", "do you mind", "hey", "hey max"
            ]
            prompt_lower = prompt.lower()
            for junk in junk_phrases:
                prompt_lower = prompt_lower.replace(junk, "")
            return prompt_lower.strip()

        cleaned_prompt = extract_core_search_phrase(prompt)
        embedding_response = client.embeddings.create(
            model="text-embedding-3-small",
            input=cleaned_prompt
        )
        embedding = embedding_response.data[0].embedding
        doc_results = perform_search({"embedding": embedding})
        all_chunks = doc_results.get("results", [])
        logger.debug(f"✅ Retrieved {len(all_chunks)} document chunks.")

        encoding = tiktoken.encoding_for_model("gpt-4o")
        max_tokens_per_batch = 4000
        current_batch = []
        token_count = 0
        batches = []

        for chunk in all_chunks:
            content = chunk.get("content", "")
            tokens = len(encoding.encode(content))
            if token_count + tokens > max_tokens_per_batch and current_batch:
                batches.append(current_batch)
                current_batch = []
                token_count = 0
            current_batch.append(chunk)
            token_count += tokens
        if current_batch:
            batches.append(current_batch)

        total_batches = len(batches)
        logger.debug(f"📦 Prepared {total_batches} token-aware batches for injection.")

        for i, batch in enumerate(batches):
            joined_text = "\n\n".join(c.get("content", "[MISSING CONTENT]") for c in batch)
            final = (i == total_batches - 1)
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=f"Document context batch {i + 1} of {total_batches} (final_batch: {final}):\n{joined_text}"
            )

        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=f"Based on document context batches 1 through {total_batches}, answer the question: {prompt}"
        )

        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant_id,
        )

        while run.status in ["queued", "in_progress"]:
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

        messages = client.beta.threads.messages.list(thread_id=thread.id)
        reply_msg = next((m for m in messages.data if m.role == "assistant" and m.content and m.content[0].type == "text"), None)
        reply = reply_msg.content[0].text.value if reply_msg else "(No assistant reply found)"

        if reply.strip() == "[handoff_to_hub]":
            logger.info("↩️ Assistant handed off back to hub")
            fallback_reply = "Just to make sure I understood — is this something related to code, documents, or something else?"
            return {"answer": fallback_reply}

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
