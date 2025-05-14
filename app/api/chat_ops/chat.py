import logging
from fastapi import APIRouter
from pydantic import BaseModel
from app.core.openai_client import chat_completion
from app.api.memory_ops.session_memory import save_message
from app.api.file_ops.search_docs import search_docs  # ✅ CORRECTED module path
import os
import requests
import uuid
import json

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

GENERAL_CONTEXT_PROJECT_ID = "00000000-0000-0000-0000-000000000000"

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
            "description": "Search user's indexed project documents by keyword",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search string used to find matching chunks",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Optional project name to filter the search scope",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search live web results using Serper API",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_memory",
            "description": "Pull relevant long-term memory from Supabase",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search phrase used to retrieve related items",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

@router.post("/chat")
def chat_with_context(request: ChatRequest):
    logger.debug(f"🔍 User prompt: {request.user_prompt}")
    logger.debug(f"👤 User ID: {request.user_id}")

    messages = [
        {
            "role": "system",
            "content": (
                "You are Max, a helpful assistant. If the user refers to a stored project like 'The Borough Guide', "
                "automatically call the 'search_docs' tool with the user's message as the query. Use the project_name field if known. "
                "You may also call 'retrieve_memory' or 'search_web' when appropriate. Use tool calls aggressively to find the best info."
            ),
        },
        {"role": "user", "content": request.user_prompt},
    ]

    # First assistant response
    response = chat_completion(
        user_id=request.user_id,
        session_id=request.session_id,
        messages=messages,
        tools=OPENAI_TOOLS,
    )

    save_message(
        user_id=request.user_id,
        session_id=request.session_id,
        role="user",
        content=request.user_prompt,
    )

    # Handle any tool calls from assistant
    if "tool_calls" in response:
        tool_messages = []
        for tool_call in response["tool_calls"]:
            fn_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])

            try:
                if fn_name == "search_docs":
                    result = search_docs(arguments)
                elif fn_name == "search_web":
                    result = f"[Simulated web search for: {arguments.get('query')}]"
                elif fn_name == "retrieve_memory":
                    result = f"[Simulated memory retrieval for: {arguments.get('query')}]"
                else:
                    result = f"⚠️ Unknown tool: {fn_name}"

                tool_messages.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": fn_name,
                    "content": result,
                })

            except Exception as e:
                tool_messages.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": fn_name,
                    "content": f"❌ Tool execution error: {str(e)}",
                })

        messages.append(response)
        messages.extend(tool_messages)

        final_response = chat_completion(
            user_id=request.user_id,
            session_id=request.session_id,
            messages=messages,
            tools=OPENAI_TOOLS,
        )

        if final_response.get("content"):
            save_message(
                user_id=request.user_id,
                session_id=request.session_id,
                role="assistant",
                content=final_response["content"],
            )
        return final_response

    # No tool used
    if response.get("content"):
        save_message(
            user_id=request.user_id,
            session_id=request.session_id,
            role="assistant",
            content=response["content"],
        )

    return response
