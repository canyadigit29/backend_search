from fastapi import APIRouter, HTTPException, Body
from app.core.openai_client import chat_completion
from app.core.supabase_client import supabase
import re
import json
from datetime import datetime

router = APIRouter()

def parse_date_from_filename(filename: str):
    # Example: 01-January-2022-Agenda-ocr or 01_January_2022_Agenda_ocr
    # Try to extract date in DD-Month-YYYY or DD_Month_YYYY
    match = re.search(r'(\d{2})[-_](\w+)[-_](\d{4})', filename)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        date = datetime.strptime(f"{day}-{month}-{year}", "%d-%B-%Y")
        return date
    except Exception:
        return None

@router.post("/item_history")
async def item_history(
    topic: str = Body(..., embed=True),
    user_id: str = Body(..., embed=True)
):
    """
    For a given topic, return a chronological history of when it appeared in previous meetings (agendas/minutes),
    with a summary of what was discussed, and only the sources used for the history.
    """
    try:
        # 1. Semantic search across all agenda/minutes files
        # (Assume all files are in the same table, filter by file name containing 'agenda' or 'minutes')
        # Use the same embedding logic as search_docs
        from app.api.file_ops.embed import embed_text
        embedding = embed_text(topic)
        # Query for relevant chunks
        rpc_args = {
            "query_embedding": embedding,
            "file_name_filter": None,
            "description_filter": None,
            "user_id_filter": user_id,
            "match_threshold": 0.3,
            "match_count": 1000
        }
        response = supabase.rpc("match_documents", rpc_args).execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=500, detail=f"Supabase RPC failed: {response.error.message}")
        matches = response.data or []
        # Only keep agenda/minutes files
        matches = [m for m in matches if re.search(r'(agenda|minut)', m.get("file_name", ""), re.I)]
        # 2. Group by file/date
        file_groups = {}
        for m in matches:
            file_name = m.get("file_name") or m.get("name") or ""
            file_name = file_name.replace("_", "-")
            date = parse_date_from_filename(file_name)
            if not date:
                continue
            key = (date, file_name)
            if key not in file_groups:
                file_groups[key] = []
            file_groups[key].append(m)
        # 3. Order by date
        ordered = sorted(file_groups.items(), key=lambda x: x[0][0])
        # 4. Summarize per meeting
        history = []
        for (date, file_name), chunks in ordered:
            # Concatenate all relevant chunks for this file/date
            text = "\n".join(c.get("content", "") for c in chunks)
            # Summarize what was discussed about the topic
            prompt = [
                {"role": "system", "content": "You are an expert at reading meeting records. Summarize what was discussed about the following topic in this meeting. Only summarize what is present in the provided text."},
                {"role": "user", "content": f"Topic: {topic}\nMeeting text:\n{text[:8000]}"}
            ]
            summary = chat_completion(prompt)
            history.append({
                "date": date.strftime("%Y-%m-%d"),
                "file_name": file_name,
                "summary": summary,
                "sources": [c.get("id") for c in chunks]
            })
        return {"history": history}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
