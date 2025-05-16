import logging, os, time, requests
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI

router = APIRouter()
logger = logging.getLogger("nerdgpt")
logger.setLevel(logging.DEBUG)

client = OpenAI()
ASSISTANT_ID = "asst_Yr6XMC7i92tCpHJNVykekJ9N"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GH_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}" if GITHUB_TOKEN else None,
    "Accept": "application/vnd.github.v3.raw"
}

# ---------- helpers ----------
def fetch_file(repo: str, path: str) -> str:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(url, headers=GH_HEADERS)
    return r.text if r.status_code == 200 else ""

def fetch_repo_snapshot(repo: str,
                        max_total: int = 200_000,
                        max_file: int = 50_000) -> str:
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1"
    r = requests.get(tree_url, headers=GH_HEADERS)
    if r.status_code != 200:
        return ""
    tree = r.json()["tree"]
    exts = (".py", ".js", ".ts", ".tsx", ".go", ".java",
            ".rb", ".rs", ".cpp", ".c", ".h", ".md", ".txt",
            ".yaml", ".yml", ".json")
    total, parts = 0, []
    for blob in (b for b in tree if b["type"] == "blob" and b["path"].endswith(exts)):
        if total >= max_total or blob["size"] > max_file:
            continue
        text = fetch_file(repo, blob["path"])
        parts.append(f"\n\n===== FILE: {blob['path']} =====\n\n{text[:max_file]}")
        total += len(text)
    return "\n".join(parts)

def run_assistant(context: List[str], user_msg: str) -> str:
    thread = client.beta.threads.create()
    tid = thread.id

    for ctx in context:
        client.beta.threads.messages.create(tid, role="user", content=ctx)

    client.beta.threads.messages.create(tid, role="user", content=user_msg)
    run = client.beta.threads.runs.create(tid, assistant_id=ASSISTANT_ID)

    while run.status not in ("completed", "failed", "cancelled", "expired"):
        time.sleep(1)
        run = client.beta.threads.runs.retrieve(tid, run.id)

    if run.status != "completed":
        raise HTTPException(status_code=500, detail="Assistant run failed")

    msg = client.beta.threads.messages.list(tid, order="desc", limit=1).data[0]
    blocks = msg.content                                  # list[TextContentBlock]
    return "\n\n".join(b.text.value for b in blocks if b.type == "text")

# ---------- API ----------
class CodeChatRequest(BaseModel):
    user_id: str
    message: str
    github_repo: str
    file_path: Optional[str] = None
    inject_full_repo: Optional[bool] = False

@router.post("/codechat")
def code_chat(req: CodeChatRequest):
    context = []
    if req.inject_full_repo:
        snap = fetch_repo_snapshot(req.github_repo)
        if snap:
            context.append(snap)
    if req.file_path:
        code = fetch_file(req.github_repo, req.file_path)
        if code:
            context.append(f"Content of `{req.file_path}`:\n\n{code}")

    try:
        reply = run_assistant(context, req.message)
    except Exception:
        logger.exception("Assistant API failure")
        raise HTTPException(status_code=500, detail="Assistant call failed")

    return {"reply": reply}
