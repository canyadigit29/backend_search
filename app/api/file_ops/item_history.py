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
    For a given topic, return all relevant chunks (with metadata) from previous meetings (agendas/minutes),
    so the frontend can display sources/files in the same way as normal semantic search.
    """
    try:
        from app.api.file_ops.embed import embed_text
        embedding = embed_text(topic)
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
        # Only keep minutes files
        matches = [m for m in matches if re.search(r'minutes', m.get("file_name", ""), re.I)]
        # Group by file/date
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
        # Order by date
        ordered = sorted(file_groups.items(), key=lambda x: x[0][0])
        # Summarize per meeting (file/date) and filter out unrelated chunks
        history = []
        filtered_matches = []
        for (date, file_name), chunks in ordered:
            text = "\n".join(c.get("content", "") for c in chunks)
            prompt = [
                {"role": "system", "content": (
                    "You are an expert at reading meeting minutes. Summarize what was discussed about the following topic in this meeting. "
                    "Only summarize what is present in the provided text. If the provided text does not actually discuss the topic, or is unrelated, respond with: 'No relevant discussion of this topic in this meeting.' "
                    "Make it clear this summary is for this specific file/date."
                )},
                {"role": "user", "content": f"Topic: {topic}\nMeeting text:\n{text[:8000]}"}
            ]
            summary = chat_completion(prompt)
            if summary.strip().lower().startswith("no relevant discussion"):
                continue  # Skip this file/date and its chunks
            history.append({
                "date": date.strftime("%Y-%m-%d"),
                "file_name": file_name,
                "summary": summary
            })
            filtered_matches.extend(chunks)
        return {"history": history, "retrieved_chunks": filtered_matches}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
