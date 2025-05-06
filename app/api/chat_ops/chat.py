from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.core.openai_client import chat_completion
from app.api.memory_ops.router_brain import route_query
from app.api.memory_ops.save_memory_entry import save_memory_entry  # ✅ Replaces store_memory
import logging
import os
import requests
import uuid
from datetime import datetime

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

class ChatRequest(BaseModel):
    user_prompt: str
    user_id: str
    session_id: str

@router.post("/chat")
async def chat_with_context(payload: ChatRequest, request: Request):
    try:
        prompt = payload.user_prompt.strip()
        logger.debug(f"📨 Incoming chat request from {request.client.host}")
        logger.debug(f"🔍 User prompt: {prompt}")
        logger.debug(f"👤 User ID: {payload.user_id}")
        logger.debug(f"🪪 Session ID: {payload.session_id}")

        try:
            uuid.UUID(str(payload.user_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format. Must be a UUID.")

        system_message = "You are Max, a logic-first assistant. No traits are currently active."

        # 🔁 Inject memory context if available
        memory_context = await route_query(
            user_query=prompt,
            session_id=payload.session_id,
            topic_name=None
        )
        memory_snippets = memory_context.get("messages", [])
        logger.debug(f"🧠 Memory source: {memory_context.get('source')}")
        logger.debug(f"🧠 Memory matches: {len(memory_snippets)}")

        # 🌐 Brave Search block
        if any(kw in prompt.lower() for kw in ["look up online", "search the web", "check online", "find online", "lookup"]):
            logger.debug("🌐 Triggering Brave Search API...")
            api_key = os.getenv("BRAVE_SEARCH_API_KEY")
            if not api_key:
                raise HTTPException(status_code=500, detail="Brave Search API key is not set.")
            response = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": api_key},
                params={"q": prompt, "count": 3}
            )
            if response.status_code != 200:
                logger.error(f"🌐 Brave API error: {response.status_code} {response.text}")
                raise HTTPException(status_code=500, detail="Failed to retrieve web search results.")

            results = response.json().get("web", {}).get("results", [])
            snippet_text = "\n".join(f"- {r['title']}: {r['url']}" for r in results)
            messages = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": f"{prompt}\n\nWeb Results:\n{snippet_text}"}
            ]
            result = chat_completion(messages)
            await save_memory_entry(payload.user_id, payload.session_id, "user", prompt)
            await save_memory_entry(payload.user_id, payload.session_id, "assistant", result)
            return {"answer": result}

        # 💬 Normal chat with memory
        memory_block = "\n".join(memory_snippets)
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": f"{memory_block}\n\n{prompt}"}
        ]
        result = chat_completion(messages)

        # ✅ Store memory for both sides
        await save_memory_entry(payload.user_id, payload.session_id, "user", prompt)
        await save_memory_entry(payload.user_id, payload.session_id, "assistant", result)

        return {"answer": result}

    except Exception as e:
        logger.exception("🚨 Error during chat processing")
        raise HTTPException(status_code=500, detail=str(e))
