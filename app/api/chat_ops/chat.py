from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.openai_client import chat_completion  # This must accept tools OR be bypassed
from app.api.memory_ops.session_memory import save_message, retrieve_memory
import logging
import os
import requests
import uuid
import json
import openai  # Use directly as fallback if wrapper is too limited

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

GENERAL_CONTEXT_PROJECT_ID = "00000000-0000-0000-0000-000000000000"

class ChatRequest(BaseModel):
    user_prompt: str
    user_id: str

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
                    "project_name": {"type": "string", "description": "Optional project name to limit scope"}
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
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_memory",
            "description": "Search past conversations and assistant memory for related information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Memory search phrase"}
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

        try:
            uuid.UUID(str(payload.user_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format. Must be a UUID.")

        system_message = "You are Max, a helpful assistant. You may use tools like 'search_docs', 'search_web', or 'retrieve_memory' when asked about topics in memory, files, or online."

        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]

        save_message(payload.user_id, GENERAL_CONTEXT_PROJECT_ID, prompt)

        try:
            logger.debug(f"📤 Sending to OpenAI: {json.dumps(messages)}")
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=messages,
                tools=OPENAI_TOOLS
            )
        except Exception as e:
            logger.exception("❌ OpenAI chat_completion failed")
            raise HTTPException(status_code=500, detail=f"Chat model failed: {str(e)}")

        if hasattr(response, "tool_calls"):
            for tool_call in response.tool_calls:
                tool_name = tool_call.function.name
                raw_args = tool_call.function.arguments
                tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                if tool_name == "search_docs":
                    from app.api.file_ops.search_docs import perform_search
                    result = perform_search(tool_args)
                    tool_response = result or "I searched your files and memory but couldn’t find anything on that topic. Try rephrasing or check if it was uploaded."

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
                    tool_response = (
                        "I searched online but couldn’t find anything helpful."
                        if not brave_data else
                        "Web Results:\n" + "\n".join(f"- {item['title']}: {item['url']}" for item in brave_data)
                    )

                elif tool_name == "retrieve_memory":
                    result = retrieve_memory(tool_args)
                    if not isinstance(result, dict) or "results" not in result:
                        logger.warning("Unexpected structure from retrieve_memory")
                        result = {"results": []}
                    results = result.get("results", [])
                    tool_response = (
                        "I don't remember anything like that."
                        if not results else
                        "\n\n".join(entry["content"] for entry in results)
                    )

                else:
                    tool_response = f"Unsupported tool call: {tool_name}"

                messages.append({"role": "function", "name": tool_name, "content": tool_response})

            result = openai.ChatCompletion.create(
                model="gpt-4",
                messages=messages
            )
            save_message(payload.user_id, GENERAL_CONTEXT_PROJECT_ID, str(result))
            return {"answer": result}

        save_message(payload.user_id, GENERAL_CONTEXT_PROJECT_ID, str(response))
        return {"answer": response}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
