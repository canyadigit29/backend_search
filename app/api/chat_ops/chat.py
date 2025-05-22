import json
import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel
import tiktoken

from app.api.memory_ops.session_memory import retrieve_memory, save_message
from app.api.file_ops.search_docs import perform_search
from app.core.supabase_client import supabase

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in environment")

client = OpenAI(api_key=openai_api_key)

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
            raise HTTPException(status_code=400, detail="Invalid user_id format. Must be a UUID.")

        save_message(
            user_id=payload.user_id,
            project_id=GENERAL_CONTEXT_PROJECT_ID,
            content=prompt,
            session_id=payload.session_id,
            speaker_role="user"
        )

        memory_result = retrieve_memory({
            "query": prompt,
            "user_id": payload.user_id,
            "session_id": payload.session_id
        })
        context = "\n".join([m["content"] for m in memory_result.get("results", [])[:5]]) if memory_result.get("results") else None

        messages = [{
            "role": "system",
            "content": (
                "You are Max, a sharp, capable assistant with a dry sense of humor and a distinctly human tone. You're candid, conversational, confident, and not afraid to be direct or witty when it helps. You're a collaborator and analyst engaging in every prompt as a conversation.\n\n"
                "Users have uploaded documents that you can search. You cannot search them directly yourself, but you can trigger a document search through the backend.\n\n"
                "- First, clarify and refine the user’s request into a precise search phrase.\n"
                "- When ready, respond with: [run_search: FINAL QUERY HERE]\n"
                "  This is the signal that tells the backend to perform the actual search for you.\n"
                "- Do not include explanations or additional commentary alongside the [run_search: ...] line.\n"
                "- After the documents are returned to you, analyze and summarize them using bullet points, headers, and clear formatting.\n"
                "- Use memory context when it helps. Never fake confidence. Prioritize clarity.    "
            )
        }]

        if context:
            messages.append({"role": "system", "content": f"Relevant memory: {context}"})
        messages.append({"role": "user", "content": prompt})

        logger.debug(f"🧐 Final message list: {json.dumps(messages, indent=2)}")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages
        )

        reply = response.choices[0].message.content.strip()
        logger.debug(f"🤖 Assistant reply: {reply}")

        # 🔎 Check for explicit search trigger
        if "[run_search:" in reply:
            search_query = reply.split("[run_search:", 1)[-1].split("]", 1)[0].strip()
            logger.info(f"🔍 Triggered document search with query: {search_query}")

            embedding_response = client.embeddings.create(
                model="text-embedding-3-large",
                input=search_query
            )
            embedding = embedding_response.data[0].embedding
            doc_results = perform_search({"embedding": embedding, "limit": 5000})
            all_chunks = doc_results.get("results", [])
            logger.debug(f"✅ Retrieved {len(all_chunks)} document chunks.")

            encoding = tiktoken.encoding_for_model("gpt-4o")
            max_tokens_per_batch = 12000  # Raised from 6000 to 12000
            current_batch = []
            token_count = 0
            total_token_count = 0  # Track total tokens across all batches
            batches = []

            for chunk in all_chunks:
                content = chunk.get("content", "")
                tokens = len(encoding.encode(content))
                if token_count + tokens > max_tokens_per_batch and current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    token_count = 0
                current_batch.append(chunk)
                total_token_count += tokens
                token_count += tokens
            if current_batch:
                batches.append(current_batch)

            total_token_count = sum(len(encoding.encode(c.get('content', ''))) for b in batches for c in b)
            logger.debug(f"🧾 Total injected document tokens: {total_token_count} across {len(batches)} batches")
            total_batches = len(batches)
            logger.debug(f"📦 Prepared {total_batches} token-aware batches for injection.")

            messages = []
            for i, batch in enumerate(batches):
                joined_text = "\n\n".join(c.get("content", "[MISSING CONTENT]") for c in batch)
                final = (i == total_batches - 1)
                messages.append({
                    "role": "user",
                    "content": f"Document context batch {i + 1} of {total_batches} (final_batch: {final}):\n{joined_text}"
                })

            messages.append({
                "role": "user",
                "content": f"Based on document context batches 1 through {total_batches}, answer the question: {search_query}"
            })

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages
            )
            reply = response.choices[0].message.content.strip()
            logger.debug(f"🧐 Assistant follow-up reply: {reply}")

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
