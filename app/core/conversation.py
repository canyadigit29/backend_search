from typing import Optional
from app.core.supabase_client import get_supabase_client

MAX_CHARS = 20000  # ~5k token rough cap


def build_transcript(chat_id: str, current_user_input: Optional[str] = None) -> str:
    """
    Build a simple transcript from the messages table for a chat_id, appending
    the current user input if provided. Mirrors the Node implementation: only user/assistant roles,
    most recent first, capped by character length.
    """
    sb = get_supabase_client()
    # Fetch messages ordered ascending by sequence_number to preserve natural order
    res = (
        sb.table("messages")
        .select("role,content,sequence_number")
        .eq("chat_id", chat_id)
        .order("sequence_number", desc=False)
        .execute()
    )
    rows = (res.data or []) if hasattr(res, "data") else (res or [])
    parts: list[str] = []
    for m in rows[::-1]:  # iterate from newest to oldest
        role = (m.get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        content = m.get("content") or ""
        parts.append(("User" if role == "user" else "Assistant") + f": {content}")
        if len("\n\n".join(parts)) > MAX_CHARS:
            break
    convo = "\n\n".join(reversed(parts))
    if current_user_input and current_user_input.strip():
        convo = (convo + "\n\n" if convo else "") + f"User: {current_user_input.strip()}"
    return convo
