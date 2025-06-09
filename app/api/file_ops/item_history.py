from fastapi import APIRouter, HTTPException, Body
from app.core.openai_client import chat_completion
from app.core.supabase_client import supabase
from app.api.file_ops.search_docs import perform_search
from app.api.file_ops.embed import embed_text
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
    For a given topic, return agenda/minutes pairs: each with the agenda match and the exact quote(s) from the minutes where the topic is discussed.
    """
    try:
        # 1. Find agenda files matching the topic (keyword search)
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        keywords = [w for w in re.split(r"\W+", topic or "") if w and w.lower() not in stopwords]
        agenda_files = (
            supabase.table("files")
            .select("*")
            .ilike("name", "%agenda%")
            .eq("user_id", user_id)
            .execute()
            .data or []
        )
        # Filter agenda files by keyword match in name or description
        agenda_matches = [
            f for f in agenda_files if any(kw.lower() in (f.get("name", "") + " " + f.get("description", "")).lower() for kw in keywords)
        ]
        if not agenda_matches:
            return {"history": [], "agenda_minutes_pairs": []}
        # 2. For each agenda, find corresponding minutes file by date
        results = []
        for agenda in agenda_matches:
            agenda_name = agenda.get("name", "")
            agenda_date = parse_date_from_filename(agenda_name)
            if not agenda_date:
                continue
            # Find minutes file with same date
            date_str = agenda_date.strftime("%d-%B-%Y")
            # Accept both - and _ separators, and case-insensitive
            minutes_files = (
                supabase.table("files")
                .select("*")
                .ilike("name", f"%{agenda_date.strftime('%d-%B-%Y')}%minutes%")
                .eq("user_id", user_id)
                .execute()
                .data or []
            )
            if not minutes_files:
                # Try with _ separator
                alt_date_str = agenda_date.strftime("%d_%B_%Y")
                minutes_files = (
                    supabase.table("files")
                    .select("*")
                    .ilike("name", f"%{alt_date_str}%minutes%")
                    .eq("user_id", user_id)
                    .execute()
                    .data or []
                )
            if not minutes_files:
                continue
            minutes_file = minutes_files[0]  # If multiple, just take the first
            # 3. For the minutes file, perform a semantic search for the topic and extract exact quotes
            embedding = embed_text(topic)
            # Use match_file_items_openai for semantic search on this file
            file_id = minutes_file["id"]
            rpc_args = {
                "query_embedding": embedding,
                "file_ids": [file_id],
                "match_count": 20,
            }
            response = supabase.rpc("match_file_items_openai", rpc_args).execute()
            if getattr(response, "error", None):
                continue
            matches = response.data or []
            # Extract exact quotes (not summary): just return the content of each match
            quotes = [m["content"] for m in matches if topic.lower() in m["content"].lower()]
            if not quotes:
                # If no exact phrase match, just take the top 1-2 matches
                quotes = [m["content"] for m in matches[:2]]
            results.append({
                "agenda": {
                    "id": agenda.get("id"),
                    "name": agenda.get("name"),
                    "description": agenda.get("description"),
                    "file_path": agenda.get("file_path"),
                },
                "minutes": {
                    "id": minutes_file.get("id"),
                    "name": minutes_file.get("name"),
                    "description": minutes_file.get("description"),
                    "file_path": minutes_file.get("file_path"),
                },
                "quotes": quotes,
            })
        return {"history": [], "agenda_minutes_pairs": results}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
