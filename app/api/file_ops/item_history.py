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
        print(f"[DEBUG] Step 1: Starting agenda semantic/hybrid search for topic: '{topic}' and user_id: {user_id}")
        # Acronym detection: all uppercase, <=5 chars, no spaces
        is_acronym = topic.isupper() and len(topic) <= 5 and ' ' not in topic
        print(f"[DEBUG] Step 1: Acronym detected: {is_acronym}")
        # Use perform_search for semantic/hybrid search on agendas
        agenda_search_args = {
            "embedding": embed_text(topic),
            "user_id_filter": user_id,
            "file_name_filter": "agenda",  # Will match any agenda file
            "match_count": 50,
            "user_prompt": topic,
            "search_query": topic
        }
        agenda_result = perform_search(agenda_search_args)
        agenda_chunks = agenda_result.get("retrieved_chunks", [])
        print(f"[DEBUG] Step 1: Found {len(agenda_chunks)} agenda chunks from semantic/hybrid search.")
        # Acronym filtering: only keep agenda chunks with exact word match if acronym
        if is_acronym:
            agenda_chunks = [chunk for chunk in agenda_chunks if re.search(rf"\\b{re.escape(topic)}\\b", chunk.get("content", ""), re.IGNORECASE)]
            print(f"[DEBUG] Step 1: After acronym exact match filtering, {len(agenda_chunks)} agenda chunks remain.")
        if not agenda_chunks:
            print(f"[DEBUG] Step 1: No agenda chunks found for topic '{topic}' and user_id '{user_id}' after filtering")
            return {"history": [
                {"summary": "There were no results for this item. This may be a new topic."}
            ], "agenda_minutes_pairs": []}
        results = []
        for agenda_chunk in agenda_chunks:
            agenda_file = agenda_chunk.get("file_name", "")
            print(f"[DEBUG] Step 2: Processing agenda chunk file: {agenda_file}")
            agenda_date = parse_date_from_filename(agenda_file)
            print(f"[DEBUG] Step 2: Parsed agenda date: {agenda_date}")
            if not agenda_date:
                print(f"[DEBUG] Step 2: Could not parse date from agenda file: {agenda_file}")
                continue
            date_str = agenda_date.strftime("%d-%B-%Y")
            alt_date_str = agenda_date.strftime("%d_%B_%Y")
            embedding = embed_text(topic)
            print(f"[DEBUG] Step 3: Embedding for topic generated. Querying minutes chunks for date: {date_str} or {alt_date_str}")
            minutes_chunks = (
                supabase.table("document_chunks")
                .select("*")
                .eq("user_id", user_id)
                .or_(f"file_name.ilike.%{date_str}%minutes%,file_name.ilike.%{alt_date_str}%minutes%")
                .execute().data or []
            )
            print(f"[DEBUG] Step 3: Found {len(minutes_chunks)} minutes chunks for date.")
            if not minutes_chunks:
                print(f"[DEBUG] Step 3: No minutes chunks found for agenda date {agenda_date}")
                continue
            tool_args = {
                "embedding": embedding,
                "user_id_filter": user_id,
                "file_name_filter": None,
                "match_count": 20
            }
            filtered_minutes = [c for c in minutes_chunks if (date_str in c.get("file_name", "") or alt_date_str in c.get("file_name", ""))]
            print(f"[DEBUG] Step 3: Filtered to {len(filtered_minutes)} minutes chunks for exact date match.")
            def dot(a, b):
                return sum(x*y for x, y in zip(a, b))
            try:
                for chunk in filtered_minutes:
                    if "embedding" in chunk:
                        chunk["score"] = dot(chunk["embedding"], embedding)
            except Exception as e:
                print(f"[DEBUG] Step 3: Error computing dot product similarity: {e}")
            filtered_minutes.sort(key=lambda c: c.get("score", 0), reverse=True)
            quotes = [c.get("content", "") for c in filtered_minutes[:2] if c.get("content")]
            print(f"[DEBUG] Step 4: Top quotes extracted: {len(quotes)}")
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
        print(f"[DEBUG] Step 5: Returning {len(results)} agenda/minutes pairs.")
        return {"history": [], "agenda_minutes_pairs": results}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
