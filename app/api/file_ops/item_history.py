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
        # 1. Keyword search for topic in agenda chunks (from document_chunks)
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        keywords = [w for w in re.split(r"\W+", topic or "") if w and w.lower() not in stopwords]
        from app.api.file_ops.search_docs import keyword_search
        agenda_chunks = keyword_search(keywords, user_id_filter=user_id)
        agenda_chunks = [c for c in agenda_chunks if re.search(r"agenda", c.get("file_name", ""), re.I)]
        if not agenda_chunks:
            return {"history": [], "agenda_minutes_pairs": []}
        results = []
        for agenda_chunk in agenda_chunks:
            agenda_file = agenda_chunk.get("file_name", "")
            agenda_date = parse_date_from_filename(agenda_file)
            if not agenda_date:
                continue
            # 2. Find all minutes chunks with matching date in file_name
            date_str = agenda_date.strftime("%d-%B-%Y")
            alt_date_str = agenda_date.strftime("%d_%B_%Y")
            # Query all minutes chunks for this user
            all_minutes_chunks = supabase.table("document_chunks").select("*") \
                .eq("user_id", user_id) \
                .execute().data or []
            minutes_chunks = [c for c in all_minutes_chunks if (
                (re.search(rf"{date_str}.*minutes", c.get("file_name", ""), re.I) or
                 re.search(rf"{alt_date_str}.*minutes", c.get("file_name", ""), re.I))
            )]
            if not minutes_chunks:
                continue
            # 3. For each minutes chunk, semantic search for topic and extract exact quotes
            embedding = embed_text(topic)
            # Filter minutes chunks by semantic similarity (dot product or use OpenAI if available)
            # For now, use keyword match as a proxy, or just include all
            quotes = []
            for chunk in minutes_chunks:
                content = chunk.get("content", "")
                if topic.lower() in content.lower():
                    quotes.append(content)
            if not quotes:
                # If no exact phrase match, just take the top 1-2 chunks
                quotes = [c.get("content", "") for c in minutes_chunks[:2]]
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
                    } for c in minutes_chunks
                ],
                "quotes": quotes,
            })
        return {"history": [], "agenda_minutes_pairs": results}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
