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
from app.core.supabase_client import supabase

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

client = OpenAI()

GENERAL_CONTEXT_PROJECT_ID = "00000000-0000-0000-0000-000000000000"

class ChatRequest(BaseModel):
    user_prompt: str
    user_id: str
    session_id: str

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
                        "description": "The exact name of the project to delete",
                    }
                },
                "required": ["project_name"],
            },
        },
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
                    "project_name": {
                        "type": "string",
                        "description": "Optional project name to limit scope",
                    },
                    "project_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of multiple project names to include in the search",
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
            "description": "Search the public internet using Brave Search API",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"}
                },
                "required": ["query"],
            },
        },
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
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_storage_files",
            "description": "Scan Supabase storage and ingest any missing or unprocessed files",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
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
            raise HTTPException(
                status_code=400, detail="Invalid user_id format. Must be a UUID."
            )

        session_response = (
            supabase.table("session_logs")
            .select("speaker_role, content")
            .eq("user_id", payload.user_id)
            .order("message_index", desc=True)
            .limit(10)
            .execute()
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Max, a helpful assistant. You may use tools like 'search_docs', "
                    "'search_web', or 'retrieve_memory' when asked about topics in memory, files, or online."
                ),
            }
        ]

        if session_response.data:
            past_messages = reversed(session_response.data)
            for row in past_messages:
                if row["speaker_role"] in ("user", "assistant"):
                    messages.append(
                        {"role": row["speaker_role"], "content": row["content"]}
                    )

        memory_result = retrieve_memory({"query": prompt})
        if memory_result.get("results"):
            memory_snippets = "\n".join([m["content"] for m in memory_result["results"]])
            messages.insert(1, {
                "role": "system",
                "content": f"Relevant past memory:\n{memory_snippets}",
            })

        # 🔍 Auto-trigger document search if certain keywords are present
        lowered_prompt = prompt.lower()
        if any(kw in lowered_prompt for kw in ["search", "find", "documents", "retrieve"]):
            try:
                embedding_response = client.embeddings.create(
                    model="text-embedding-3-small",
                    input=prompt
                )
                embedding = embedding_response.data[0].embedding
                doc_results = perform_search({"embedding": embedding})
                if doc_results.get("results"):
                    doc_snippets = "\n".join([d["content"] for d in doc_results["results"]])
                    messages.insert(1, {
                        "role": "system",
                        "content": f"Relevant document excerpts:\n{doc_snippets}"
                    })
                elif doc_results.get("error") or doc_results.get("message"):
                    messages.insert(1, {
                        "role": "system",
                        "content": f"[search_docs output]: {doc_results.get('error') or doc_results.get('message')}"
                    })
            except Exception as e:
                logger.warning(f"⚠️ search_docs failed: {e}")
                messages.insert(1, {
                    "role": "system",
                    "content": f"[search_docs exception]: {str(e)}"
                })

        messages.append({"role": "user", "content": prompt})
        save_message(payload.user_id, payload.session_id, prompt)

        response = client.chat.completions.create(
            model="gpt-4", messages=messages, tools=OPENAI_TOOLS
        )
        logger.debug(f"🧪 OpenAI raw response: {response}")

        tool_calls = (
            response.choices[0].message.tool_calls if response.choices else None
        )

        if tool_calls:
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                raw_args = tool_call.function.arguments
                tool_args = (
                    json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                )
                logger.debug(f"🔨 Tool call: {tool_name} → args: {tool_args}")

                if tool_name == "sync_storage_files":
                    storage = supabase.storage.from_("maxgptstorage")
                    folders = storage.list(payload.user_id, {"limit": 100})
                    added = []

                    for folder in folders:
                        project = folder["name"]
                        project_id_result = (
                            supabase.table("projects")
                            .select("id")
                            .eq("user_id", payload.user_id)
                            .eq("name", project)
                            .maybe_single()
                            .execute()
                        )

                        if not project_id_result or not getattr(project_id_result, "data", None):
                            continue

                        project_id = project_id_result.data["id"]
                        path_prefix = f"{payload.user_id}/{project}/"
                        files = storage.list(path_prefix, {"limit": 100})

                        for f in files:
                            name = f["name"]
                            if not name or name.startswith("."):
                                continue

                            file_path = f"{path_prefix}{name}"
                            exists = (
                                supabase.table("files")
                                .select("id, ingested")
                                .eq("file_path", file_path)
                                .maybe_single()
                                .execute()
                            )

                            if not exists.data:
                                file_id = str(uuid.uuid4())
                                supabase.table("files").insert(
                                    {
                                        "id": file_id,
                                        "file_path": file_path,
                                        "file_name": name,
                                        "project_id": project_id,
                                        "user_id": payload.user_id,
                                        "uploaded_at": datetime.utcnow().isoformat(),
                                        "ingested": False,
                                        "ingested_at": None,
                                    }
                                ).execute()
                                added.append(file_path)
                            else:
                                file_id = exists.data["id"]

                            if not exists.data or not exists.data.get("ingested"):
                                process_file(
                                    file_path=file_path,
                                    file_id=file_id,
                                    user_id=payload.user_id,
                                )

                    tool_response = (
                        f"✅ Sync complete. Files added or reprocessed: {len(added)}"
                    )

                else:
                    tool_response = f"Unsupported tool call: {tool_name}"

                messages.append(
                    {"role": "function", "name": tool_name, "content": tool_response}
                )

            followup = client.chat.completions.create(model="gpt-4", messages=messages)
            reply = (
                followup.choices[0].message.content
                if followup.choices
                else "(No reply)"
            )
            save_message(payload.user_id, payload.session_id, reply)
            return {"answer": reply}

        reply = (
            response.choices[0].message.content if response.choices else "(No reply)"
        )
        save_message(payload.user_id, payload.session_id, reply)
        return {"answer": reply}

    except Exception as e:
        logger.exception("🚨 Uncaught error in /chat route")
        return {"error": str(e)}
