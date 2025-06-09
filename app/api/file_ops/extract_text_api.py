from fastapi import APIRouter, HTTPException, Query, Body
from app.core.extract_text import extract_text
from app.core.supabase_client import supabase
from app.core.openai_client import chat_completion
import os
import tempfile
import traceback
import json

router = APIRouter()

@router.get("/extract_text")
async def api_extract_text(file_path: str = Query(...)):
    """
    Downloads a file from Supabase Storage, extracts text if PDF, and returns the text and file name.
    Improved: Always saves as .pdf, prints debug info, and logs full traceback on error.
    """
    try:
        # Download file from Supabase Storage
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "files")
        file_response = supabase.storage.from_(bucket).download(file_path)
        if not file_response:
            print(f"[ERROR] File not found in storage: {file_path}")
            raise HTTPException(status_code=404, detail="File not found in storage.")
        file_bytes = file_response  # FIX: supabase-py returns bytes, not a file-like object
        print(f"[DEBUG] Downloaded {len(file_bytes)} bytes from Supabase for {file_path}")
        # Always save as .pdf
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(file_bytes)
            tmp_file_path = tmp_file.name
        print(f"[DEBUG] Temp file path: {tmp_file_path}")
        # Extract text
        text = extract_text(tmp_file_path)
        file_name = os.path.basename(file_path)
        os.remove(tmp_file_path)
        return {"text": text, "file_name": file_name}
    except Exception as e:
        print(f"[ERROR] Exception in extract_text: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to extract text: {str(e)}")

@router.post("/extract_checklist")
async def extract_checklist(text: str = Body(..., embed=True)):
    """
    Accepts raw text and returns a checklist of actionable/contextual items using the LLM.
    Each item will have a 'label' and 'text'.
    """
    prompt = [
        {"role": "system", "content": "You are an expert at reading documents. Read the following document and return a JSON array. Each array item should represent a distinct actionable or contextual item, with a 'label' (short description) and 'text' (the full text of the item). When segmenting, treat headings, bullet points, numbered lists, and clear line breaks as boundaries for new items. If you see a heading or section title, start a new item. If the document contains lists, treat each list item as a separate checklist item. Only output the JSON array, no explanation or markdown."},
        {"role": "user", "content": text[:12000]}
    ]
    try:
        llm_response = chat_completion(prompt)
        checklist = json.loads(llm_response)
        # Validate structure
        if not isinstance(checklist, list) or not all(isinstance(item, dict) and 'label' in item and 'text' in item for item in checklist):
            raise ValueError("LLM did not return a valid checklist array")
        return {"checklist": checklist}
    except Exception as e:
        print(f"[ERROR] Checklist extraction failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to extract checklist: {str(e)}")
