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
        # Step 1: Keyword search for topic in agenda chunks (content, not file name)
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        keywords = [w for w in re.split(r"\W+", topic or "") if w and w.lower() not in stopwords]
        from app.api.file_ops.search_docs import keyword_search, perform_search
        agenda_chunks = (
            supabase.table("document_chunks")
            .select("*")
            .eq("user_id", user_id)
            .ilike("file_name", "%agenda%")
            .or_("{}".format(",".join([f"content.ilike.%{kw}%" for kw in keywords])))
            .execute().data or []
        )
        if not agenda_chunks:
            return {"history": [
                {"summary": "There were no results for this item. This may be a new topic."}
            ], "agenda_minutes_pairs": []}
        results = []
        for agenda_chunk in agenda_chunks:
            agenda_file = agenda_chunk.get("file_name", "")
            agenda_date = parse_date_from_filename(agenda_file)
            if not agenda_date:
                continue
            date_str = agenda_date.strftime("%d-%B-%Y")
            alt_date_str = agenda_date.strftime("%d_%B_%Y")
            # Step 2: For each agenda, do a full semantic search on minutes for that date (no keyword boost)
            embedding = embed_text(topic)
            # Query minutes chunks for this user and date
            minutes_chunks = (
                supabase.table("document_chunks")
                .select("*")
                .eq("user_id", user_id)
                .or_(f"file_name.ilike.%{date_str}%minutes%,file_name.ilike.%{alt_date_str}%minutes%")
                .execute().data or []
            )
            if not minutes_chunks:
                continue
            # Full semantic search (no keyword boost)
            # Use perform_search with only embedding and file_name filter
            tool_args = {
                "embedding": embedding,
                "user_id_filter": user_id,
                "file_name_filter": None,  # We'll filter by file_name below
                "match_count": 20
            }
            # Filter minutes_chunks to just those for this date
            filtered_minutes = [c for c in minutes_chunks if (date_str in c.get("file_name", "") or alt_date_str in c.get("file_name", ""))]
            # Compute dot product similarity manually (since we have the embedding)
            def dot(a, b):
                return sum(x*y for x, y in zip(a, b))
            try:
                for chunk in filtered_minutes:
                    if "embedding" in chunk:
                        chunk["score"] = dot(chunk["embedding"], embedding)
            except Exception:
                pass
            filtered_minutes.sort(key=lambda c: c.get("score", 0), reverse=True)
            # Extract top quotes (top 2 chunks)
            quotes = [c.get("content", "") for c in filtered_minutes[:2] if c.get("content")]
            results.append({
                "agenda": {
                    "file_name": agenda_chunk.get("file_name"),
                    "content": agenda_chunk.get("content"),
                    "chunk_index": agenda_chunk.get("chunk_index"),
                },
                "minutes": [
                    {
                        "file_name": c.get("file_name"),
                        "chunk_index": c.get("chunk_index"),
                        "content": c.get("content"),
                        "score": c.get("score", 0),
                    } for c in filtered_minutes
                ],
                "quotes": quotes,
            })
        return {"history": [], "agenda_minutes_pairs": results}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
