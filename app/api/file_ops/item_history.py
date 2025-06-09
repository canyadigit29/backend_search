from fastapi import APIRouter, HTTPException, Body
from app.core.openai_client import chat_completion
from app.core.supabase_client import supabase
from app.api.file_ops.search_docs import perform_search
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
        tool_args = {
            "embedding": None,  # perform_search will embed if needed
            "user_id_filter": user_id,
            "file_name_filter": None,
            "description_filter": None,
            "start_date": None,
            "end_date": None,
            "match_count": 1000,
            "search_query": topic,
            "user_prompt": topic
        }
        result = perform_search(tool_args)
        matches = result.get("retrieved_chunks", [])
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
        # --- Two-pass approach: First, ask LLM to label each chunk as Relevant/Not Relevant ---
        if not matches:
            return {"history": [], "retrieved_chunks": []}
        chunk_texts = [f"File: {m.get('file_name', '')}\nChunk: {m.get('content', '')}" for m in matches]
        labeling_prompt = [
            {"role": "system", "content": (
                "You are an expert at reading meeting minutes. For each chunk below, determine if it contains information directly relevant to the topic. "
                "If it is relevant, respond with 'Relevant'. If not, respond with 'Not Relevant'. "
                "Respond with a JSON array of 'Relevant' or 'Not Relevant' for each chunk in order."
            )},
            {"role": "user", "content": f"Topic: {topic}\nChunks:\n" + "\n---\n".join(chunk_texts)[:12000]}
        ]
        labeling_response = chat_completion(labeling_prompt)
        try:
            relevance_labels = json.loads(labeling_response)
        except Exception:
            relevance_labels = ["Relevant"] * len(matches)  # fallback: treat all as relevant
        # Filter matches to only relevant ones
        relevant_matches = [m for m, label in zip(matches, relevance_labels) if label.lower() == "relevant"]
        if not relevant_matches:
            return {"history": [{"summary": "No relevant information found in any source."}], "retrieved_chunks": []}
        # --- Second pass: Summarize only relevant chunks ---
        all_text = "\n\n---\n\n".join(f"File: {m.get('file_name', '')}\nChunk: {m.get('content', '')}" for m in relevant_matches)
        prompt = [
            {"role": "system", "content": (
                "You are an expert at reading meeting minutes. The following are chunks of text from different meeting minutes files. "
                "Each chunk may be much larger than the information relevant to the topic. "
                "For each chunk, extract and summarize ONLY the information that is directly relevant to the topic. "
                "If a chunk contains no relevant information, do not include it in the summary. "
                "For each file/chunk, output a summary in the format: \nFile: <file_name>\nSummary: <summary of relevant info>. "
                "At the end, provide a brief overall summary of what was discussed about the topic across all files. "
                "Quote or extract the exact relevant sentences if possible."
            )},
            {"role": "user", "content": f"Topic: {topic}\nMeeting chunks:\n{all_text[:12000]}"}
        ]
        summary = chat_completion(prompt)
        # Add file_metadata to each chunk for frontend grouping
        for chunk in relevant_matches:
            if 'file_metadata' not in chunk:
                chunk['file_metadata'] = {
                    'id': chunk.get('file_id'),
                    'name': chunk.get('file_name'),
                    'type': 'pdf',
                }
        return {"history": [{"summary": summary}], "retrieved_chunks": relevant_matches}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
