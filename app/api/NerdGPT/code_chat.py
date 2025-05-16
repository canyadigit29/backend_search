import logging
import os
import time
from datetime import datetime
from typing import List, Dict, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI

router = APIRouter()
logger = logging.getLogger("nerdgpt")
logger.setLevel(logging.DEBUG)

# ---- OpenAI client ----
client = OpenAI()
ASSISTANT_ID = "asst_Yr6XMC7i92tCpHJNVykekJ9N"

# ---- GitHub token ----
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

class CodeChatRequest(BaseModel):
    user_id: str = Field(..., description="User UUID")
    message: str = Field(..., description="User prompt")
    github_repo: str = Field(..., description="owner/repo")
    file_path: Optional[str] = Field(None, description="Path of a single file to inject")
    inject_full_repo: Optional[bool] = False

# ---- GitHub helpers ----
GH_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3.raw"
} if GITHUB_TOKEN else {}

def fetch_file(repo: str, path: str) -> str:
    """Return raw text of a file; empty string if not found."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=GH_HEADERS)
    if r.status_code == 200:
        return r.text
    logger.warning("GitHub fetch %s returned %s", url, r.status_code)
    return ""

# ---- OpenAI Assistants helpers ----
def run_assistant_with_messages(system_messages: List[Dict[str, str]], user_message: str) -> str:
    """Create a thread, send messages, run assistant, return assistant reply."""
    # 1. Create thread
    thread = client.beta.threads.create()
    thread_id = thread.id

    # 2. Add system / context messages
    for m in system_messages:
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=m["content"]
        )

    # 3. Add user prompt
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_message
    )

    # 4. Run assistant
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID
    )

    # 5. Poll until completed
    while run.status not in ("completed", "failed", "cancelled", "expired"):
        time.sleep(1)
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)

    if run.status != "completed":
        logger.error("Run ended with status %s", run.status)
        raise HTTPException(status_code=500, detail="Assistant run failed")

    # 6. Retrieve latest assistant message
    msgs = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=1)
    if msgs.data:
        content = msgs.data[0].content
        if isinstance(content, list):
            text_parts = [blk["text"]["value"] for blk in content if blk.get("type")=="text"]
            return "\n\n".join(text_parts)
        return content if isinstance(content,str) else str(content)
    return "(no response)"

@router.post("/codechat")
def code_chat(req: CodeChatRequest):
    # Build context messages
    context_msgs = []

    if req.file_path:
        code_text = fetch_file(req.github_repo, req.file_path)
        if code_text:
            context_msgs.append({
                "content": f"Here is the content of `{req.file_path}` from repo `{req.github_repo}`:\n\n{code_text}"
            })

    if req.inject_full_repo:
        context_msgs.append({
            "content": f"The entire repository `{req.github_repo}` should be considered as context."
        })

    try:
        reply = run_assistant_with_messages(context_msgs, req.message)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Assistant API call failed")
        raise HTTPException(status_code=500, detail="Assistant call failed")

    return {"reply": reply}