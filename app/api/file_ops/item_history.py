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
        # Instead of summarizing per file/date, summarize all relevant chunks at once
        if not matches:
            return {"history": [], "retrieved_chunks": []}
        # Compose a single prompt for all matches
        all_text = "\n\n---\n\n".join(f"File: {m.get('file_name', '')}\nChunk: {m.get('content', '')}" for m in matches)
        prompt = [
            {"role": "system", "content": (
                "You are an expert at reading meeting minutes. The following are chunks of text from different meeting minutes files. "
                "Each chunk may be much larger than the information relevant to the topic. "
                "For each chunk, extract and summarize ONLY the information that is directly relevant to the topic. "
                "If a chunk contains no relevant information, do not include it in the summary. "
                "For each file/chunk, output a summary in the format: \nFile: <file_name>\nSummary: <summary of relevant info or 'No relevant info'>. "
                "At the end, provide a brief overall summary of what was discussed about the topic across all files. "
            )},
            {"role": "user", "content": f"Topic: {topic}\nMeeting chunks:\n{all_text[:12000]}"}
        ]
        summary = chat_completion(prompt)
        # Return the summary and all retrieved chunks for frontend display
        return {"history": [{"summary": summary}], "retrieved_chunks": matches}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
