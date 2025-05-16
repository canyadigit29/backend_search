import logging
import os
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from openai import OpenAI

from app.core.supabase_client import supabase

router = APIRouter()
logger = logging.getLogger("nerdgpt")
logger.setLevel(logging.DEBUG)

client = OpenAI()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

class CodeChatRequest(BaseModel):
    user_id: str
    message: str
    github_repo: str
    file_path: str | None = None
    inject_full_repo: bool | None = False


def fetch_file_from_github(repo: str, path: str) -> str:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw"
    }
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.text
    logger.warning("GitHub fetch %s returned %s", url, r.status_code)
    return ""


@router.post("/codechat")
def code_chat(req: CodeChatRequest):
    # Build thread history (last 10)
    prior = (
        supabase.table("code_session_logs")
        .select("speaker_role, content")
        .eq("user_id", req.user_id)
        .order("message_index", desc=False)
        .limit(10)
        .execute()
    )

    messages = []
    if prior.data:
        messages.extend(
            {"role": row["speaker_role"], "content": row["content"]}
            for row in prior.data
        )

    # Repo context
    if req.inject_full_repo:
        messages.append({
            "role": "system",
            "content": f"You have full context of repo `{req.github_repo}`. Answer questions using that code."
        })

    if req.file_path:
        code_text = fetch_file_from_github(req.github_repo, req.file_path)
        if code_text:
            messages.append({
                "role": "system",
                "content": f"Content of `{req.file_path}` from `{req.github_repo}`:\n\n{code_text}"
            })

    messages.append({"role": "user", "content": req.message})

    try:
        chat_resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages
        )
        reply = chat_resp.choices[0].message.content
    except Exception:
        logger.exception("Chat completion failed")
        raise HTTPException(status_code=500, detail="NerdGPT failed to respond")

    base_idx = len(messages) - 1
    supabase.table("code_session_logs").insert([
        {
            "user_id": req.user_id,
            "message_index": base_idx,
            "speaker_role": "user",
            "content": req.message,
            "created_at": datetime.utcnow().isoformat()
        },
        {
            "user_id": req.user_id,
            "message_index": base_idx + 1,
            "speaker_role": "assistant",
            "content": reply,
            "created_at": datetime.utcnow().isoformat()
        }
    ]).execute()

    return {"reply": reply}