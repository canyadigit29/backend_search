import logging
import os
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from openai import OpenAI
import requests

from app.core.supabase_client import supabase

router = APIRouter()
logger = logging.getLogger("nerdgpt")
logger.setLevel(logging.DEBUG)

client = OpenAI()
NERDGPT_ID = os.getenv("NERDGPT_ID", "asst_Yr6XMC7i92tCpHJNVykekJ9N")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

class CodeChatRequest(BaseModel):
    user_id: str
    message: str
    github_repo: str  # e.g. "username/repo"
    file_path: str = None  # Optional file to pull context from

def fetch_file_from_github(repo: str, file_path: str) -> str:
    url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw"
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.text
    else:
        logger.warning(f"Failed to fetch file from GitHub: {response.status_code}")
        return ""

@router.post("/codechat")
def code_chat(request: CodeChatRequest):
    user_id = request.user_id
    user_message = request.message
    repo = request.github_repo
    file_path = request.file_path

    # Optional GitHub file context
    file_context = ""
    if file_path:
        file_context = fetch_file_from_github(repo, file_path)

    # Pull prior messages from session log
    thread_messages = []
    prior_messages = (
        supabase.table("code_session_logs")
        .select("speaker_role, content")
        .eq("user_id", user_id)
        .order("message_index", desc=False)
        .limit(10)
        .execute()
    )
    if prior_messages.data:
        for row in prior_messages.data:
            thread_messages.append({"role": row["speaker_role"], "content": row["content"]})

    if file_context:
        thread_messages.append({
            "role": "user",
            "content": f"Here's the content of `{file_path}` from repo `{repo}`:\n\n{file_context}"
        })

    # Append new message
    thread_messages.append({"role": "user", "content": user_message})

    try:
        # Create a new thread
        thread = client.beta.threads.create()

        # Add all messages
        for msg in thread_messages:
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role=msg["role"],
                content=msg["content"]
            )

        # Run the assistant
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=NERDGPT_ID
        )

        # Wait for run to complete
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled"]:
                raise Exception(f"Run failed with status: {run_status.status}")
            time.sleep(1)

        # Get assistant reply
        messages = client.beta.threads.messages.list(thread_id=thread.id)
        final_message = next((m for m in reversed(messages.data) if m.role == "assistant"), None)
        reply = final_message.content[0].text.value if final_message else "[No reply received]"

    except Exception as e:
        logger.exception("Failed to call NerdGPT assistant")
        raise HTTPException(status_code=500, detail=f"NerdGPT failed to respond: {str(e)}")

    # Save both user and assistant messages
    base_index = len(thread_messages) - 1
    supabase.table("code_session_logs").insert([
        {
            "user_id": user_id,
            "message_index": base_index,
            "speaker_role": "user",
            "content": user_message,
            "created_at": datetime.utcnow().isoformat()
        },
        {
            "user_id": user_id,
            "message_index": base_index + 1,
            "speaker_role": "assistant",
            "content": reply,
            "created_at": datetime.utcnow().isoformat()
        }
    ]).execute()

    return {"reply": reply}
