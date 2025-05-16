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
            fallback_reply = "🤖 I'm not sure which assistant should help. Is this a coding question, a document search, or something else?"
            return {"answer": fallback_reply}

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

            # 🔍 Log score stats for filter tuning
            scores = [c["score"] for c in chunks if "score" in c]
            if scores:
                logger.debug(
                    f"📊 Score stats — total: {len(scores)}, min: {min(scores):.4f}, max: {max(scores):.4f}, avg: {sum(scores)/len(scores):.4f}"
                )
                sorted_chunks = sorted(chunks, key=lambda x: x["score"])
                for i, low_chunk in enumerate(sorted_chunks[:3]):
                    logger.debug(f"🧹 Low score sample {i+1} — score: {low_chunk['score']:.4f}, preview: {low_chunk.get('content', '')[:150]}")
            else:
                logger.debug("📭 No score data available for results")

            logger.debug(f"📦 Found {len(chunks)} document chunks from search.")
            if chunks:
                logger.debug(f"🧾 First result keys: {list(chunks[0].keys())}")
                logger.debug(f"📄 Sample content preview: {chunks[0].get('content', 'NO CONTENT')[:200]}")

            if len(chunks) > 40:
                clarification_msg = "I found a large number of potentially relevant records. Could you help narrow it down — perhaps by specifying a year, topic, or department?"
                return {"answer": clarification_msg}

            encoding = tiktoken.encoding_for_model("gpt-4o")
            max_tokens_per_batch = 4000
            batch = []
            token_count = 0
            batch_index = 1

            for chunk in chunks:
                content = chunk.get("content", "")
                tokens = len(encoding.encode(content))
                if token_count + tokens > max_tokens_per_batch and batch:
                    joined_text = "\n\n".join(c.get("content", "[MISSING CONTENT]") for c in batch)
                    logger.debug(f"📤 Injecting batch {batch_index} — {token_count} tokens")
                    client.beta.threads.messages.create(
                        thread_id=thread.id,
                        role="user",
                        content=f"Document context batch {batch_index}:\n{joined_text}"
                    )
                    batch_index += 1
                    batch = []
                    token_count = 0
                batch.append(chunk)
                token_count += tokens

            if batch:
                joined_text = "\n\n".join(c.get("content", "[MISSING CONTENT]") for c in batch)
                logger.debug(f"📤 Injecting batch {batch_index} — {token_count} tokens")
                client.beta.threads.messages.create(
                    thread_id=thread.id,
                    role="user",
                    content=f"Document context batch {batch_index}:\n{joined_text}"
                )

            client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=f"Based on all the previous document context, answer the question: {prompt}"
            )
        else:
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
        reply_msg = next((m for m in messages.data if m.role == "assistant"), None)
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
