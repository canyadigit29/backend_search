from fastapi import APIRouter, HTTPException, Body
from app.core.openai_client import chat_completion
from app.core.supabase_client import supabase
from app.api.file_ops.search_docs import perform_search
from app.api.file_ops.embed import embed_text
from app.core.llm_answer_extraction import extract_answer_from_chunks
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
    topic: str = Body(..., embed=True)
):
    """
    For a given topic, return the most relevant quote from minutes files where the topic is discussed, using LLM extraction and keyword boost logic.
    """
    try:
        print(f"[DEBUG] Step 1: Starting minutes semantic/hybrid search for topic: '{topic}' (no user filter)")
        is_acronym = topic.isupper() and len(topic) <= 5 and ' ' not in topic
        print(f"[DEBUG] Step 1: Acronym detected: {is_acronym}")
        # Use perform_search to get hybrid/boosted results (semantic + keyword boost)
        search_args = {
            "embedding": embed_text(topic),
            "user_id_filter": None,
            "file_name_filter": "%minute%",
            "match_count": 20,
            "user_prompt": topic,
            "search_query": topic
        }
        search_result = perform_search(search_args)
        minutes_chunks = search_result.get("retrieved_chunks", [])
        print(f"[DEBUG] Step 2: Found {len(minutes_chunks)} minutes chunks from perform_search.")
        # Acronym filtering: only keep chunks with exact word match if acronym
        if is_acronym:
            minutes_chunks = [chunk for chunk in minutes_chunks if re.search(rf"\\b{re.escape(topic)}\\b", chunk.get("content", ""), re.IGNORECASE)]
            print(f"[DEBUG] Step 2: After acronym exact match filtering, {len(minutes_chunks)} chunks remain.")
        if not minutes_chunks:
            return {"history": [
                {"summary": "There were no results for this item. This may be a new topic."}
            ], "quotes": []}
        # Use LLM to extract the most relevant quote from the top N chunks
        top_chunks = [c for c in minutes_chunks[:5] if c.get("content")]
        chunk_texts = [c.get("content", "") for c in top_chunks]
        llm_prompt = (
            "You are an expert at reading meeting minutes. "
            "Given the following topic and excerpts from meeting minutes, quote only the single most relevant sentence or short passage (1-3 sentences) that directly addresses the topic. "
            "If nothing is relevant, say 'No relevant information found.'\n"
            f"Topic: {topic}\n"
            f"Minutes Excerpts:\n" + "\n---\n".join(chunk_texts)
        )
        print(f"[DEBUG] Step 3: Sending top chunks to LLM for fine-grained quote extraction.")
        llm_quote = extract_answer_from_chunks(topic, chunk_texts)
        print(f"[DEBUG] Step 4: LLM returned quote: {llm_quote[:200]}")
        return {"history": [], "quotes": [llm_quote], "retrieved_chunks": top_chunks}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
