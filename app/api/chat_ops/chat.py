from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.api.memory_ops.session_memory import save_message, retrieve_memory
from app.core.supabase_client import supabase
import logging
import os
import requests
import uuid
import json
from openai import OpenAI

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

client = OpenAI()

GENERAL_CONTEXT_PROJECT_ID = "00000000-0000-0000-0000-000000000000"

class ChatRequest(BaseModel):
    user_prompt: str
    user_id: str

# Tool definitions for OpenAI
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "delete_project",
            "description": "Delete a project and all its associated data (requires exact project name match)",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {
                        "type": "string",
                        "description": "The exact name of the project to delete"
                    }
                },
                "required": ["project_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search stored documents and memory for relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                    "project_name": {"type": "string", "description": "Optional project name to limit scope"},
                    "project_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of multiple project names to include in the search"
                    }
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

        # Build message history from session_logs (short-term memory)
        session_response = (
            supabase.table("session_logs")
            .select("speaker_role, content")
            .eq("user_id", payload.user_id)
            .order("message_index", desc=True)
            .limit(10)
            .execute()
        )

        messages = [{
            "role": "system",
            "content": (
                "You are Max, a helpful assistant. You may use tools like 'search_docs', "
                "'search_web', or 'retrieve_memory' when asked about topics in memory, files, or online."
            )
        }]

        if session_response.data:
            past_messages = reversed(session_response.data)
            for row in past_messages:
                if row["speaker_role"] in ("user", "assistant"):
                    messages.append({"role": row["speaker_role"], "content": row["content"]})

        # Optional: Long-term memory injection
        memory_result = retrieve_memory({"query": prompt})
        if memory_result.get("results"):
            memory_snippets = "\n".join([m["content"] for m in memory_result["results"]])
            messages.insert(1, {
                "role": "system",
                "content": f"Relevant past memory:\n{memory_snippets}"
            })

        # Append current prompt
        messages.append({"role": "user", "content": prompt})
        save_message(payload.user_id, GENERAL_CONTEXT_PROJECT_ID, prompt)

        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            tools=OPENAI_TOOLS
        )
        logger.debug(f"🧪 OpenAI raw response: {response}")

        tool_calls = response.choices[0].message.tool_calls if response.choices else None

        if tool_calls:
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                raw_args = tool_call.function.arguments
                tool_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                logger.debug(f"🔨 Tool call: {tool_name} → args: {tool_args}")

                if tool_name == "search_docs":
                    from app.api.file_ops.search_docs import perform_search
                    result = perform_search(tool_args)
                    tool_response = result or (
                        "I searched your files and memory but couldn’t find anything on that topic. "
                        "Try rephrasing or check if it was uploaded."
                    )

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

                elif tool_name == "delete_project":
                    from app.api.project_ops.project import delete_project_by_name
                    project_name = tool_args.get("project_name", "").strip()
                    if not project_name:
                        tool_response = "You must specify the full name of the project you want to delete."
                    else:
                        try:
                            deleted = await delete_project_by_name(project_name)
                            if deleted.get("success"):
                                tool_response = f"✅ Project '{project_name}' has been deleted."
                            else:
                                tool_response = deleted.get("error") or "Something went wrong deleting the project."
                        except Exception as e:
                            tool_response = f"Error while deleting project: {str(e)}"

                else:
                    tool_response = f"Unsupported tool call: {tool_name}"

                messages.append({"role": "function", "name": tool_name, "content": tool_response})

            followup = client.chat.completions.create(
                model="gpt-4",
                messages=messages
            )
            reply = followup.choices[0].message.content if followup.choices else "(No reply)"
            save_message(payload.user_id, GENERAL_CONTEXT_PROJECT_ID, reply)
            return {"answer": reply}

        # fallback: no tools triggered
        reply = response.choices[0].message.content if response.choices else "(No reply)"
        save_message(payload.user_id, GENERAL_CONTEXT_PROJECT_ID, reply)
        return {"answer": reply}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
