from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.core.openai_client import chat_completion
import logging
import os
import requests
import uuid

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
            return {"answer": result}

        # 💬 Normal chat (no memory)
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]
        result = chat_completion(messages)
        return {"answer": result}

    except Exception as e:
        logger.exception("🚨 Error during chat processing")
        raise HTTPException(status_code=500, detail=str(e))
