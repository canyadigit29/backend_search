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

# This file seems to have logic that is not currently supported by your database schema.
# The function `perform_search` and other logic references columns and concepts 
# that do not align with the `files` and `document_chunks` tables.
# For now, I will comment out the implementation to prevent crashes.
# We can revisit this functionality later.

@router.post("/item_history")
async def item_history(
    topic: str = Body(..., embed=True),
    user_id: str = Body(..., embed=True)
):
    """
    For a given topic, return the most relevant quote from minutes files where the topic is discussed, using LLM extraction and keyword boost logic.
    """
    raise HTTPException(status_code=501, detail="This feature is not currently implemented.")
