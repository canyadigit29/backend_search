from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.openai_client import chat_completion
import logging
import os
import requests
import uuid
import json

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

class ChatRequest(BaseModel):
    user_prompt: str
    user_id: str
    session_id: str

# Tool definitions for OpenAI
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search stored documents and memory for relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                    "project_id": {"type": "string", "description": "Optional project ID to limit scope"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the public internet using Brave Search API",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"}
                },
                "required": ["query"]
            }
        }
    }
]

@router.post("/chat")
async def chat_with_context(payload: ChatRequest):
    try:
        prompt = payload.user_prompt.strip()
        logger.debug(f"🔍 User prompt: {prompt}")
        logger.debug(f"👤 User ID: {payload.user_id}")
        logger.debug(f"🪪 Session ID: {payload.session_id}")

        try:
            uuid.UUID(str(payload.user_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format. Must be a UUID.")

        system_message = "You are Max, a helpful assistant. You may use tools like 'search_docs' or 'search_web' when asked about topics in memory, files, or online."

        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]

        response = chat_completion(messages, tools=OPENAI_TOOLS)

        if hasattr(response, "tool_calls"):
            for tool_call in response.tool_calls:
                tool_name = tool_call.function.name
                raw_args = tool_call.function.arguments
                tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                if tool_name == "search_docs":
                    from app.api.file_ops.search_docs import perform_search  # must implement this
                    result = perform_search(tool_args)
                    if not result:
                        tool_response = "I searched your files and memory but couldn’t find anything on that topic. Try rephrasing or check if it was uploaded."
                    else:
                        tool_response = result

                elif tool_name == "search_web":
                    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
                    if not api_key:
                        raise HTTPException(status_code=500, detail="Brave Search API key is not set.")
                    r = requests.get(
                        "https://api.search.brave.com/res/v1/web/search",
                        headers={"X-Subscription-Token": api_key},
                        params={"q": tool_args["query"], "count": 3}
                    )
                    brave_data = r.json().get("web", {}).get("results", [])
                    if not brave_data:
                        tool_response = "I searched online but couldn’t find anything helpful."
                    else:
                        tool_response = "Web Results:\n" + "\n".join(f"- {item['title']}: {item['url']}" for item in brave_data)

                else:
                    tool_response = f"Unsupported tool call: {tool_name}"

                messages.append({"role": "function", "name": tool_name, "content": tool_response})

            result = chat_completion(messages)
            return {"answer": result}

        # fallback: no tools called
        return {"answer": response}

    except Exception as e:
        logger.exception("🚨 Error during chat processing")
        raise HTTPException(status_code=500, detail=str(e))
