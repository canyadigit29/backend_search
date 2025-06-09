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
    Now iteratively refines the checklist by asking the LLM to compare its output to the document and add missed items.
    """
    base_prompt = [
        {"role": "system", "content": (
            "You are an expert at reading documents. The following document text contains special layout clues: "
            "---PAGE N--- marks page breaks, ###HEADING### marks headings, and lines starting with * or - are bullet points. "
            "Numbered lists may appear as lines starting with a number and a period. "
            "These clues indicate the original document's structure. "
            "Determine by context and layout what separate items are listed in the document, and return a JSON array where each item has a 'label' (short description) and 'text' (the full text of the item). "
            "Only output the JSON array, no explanation or markdown."
        )},
        {"role": "user", "content": text[:12000]}
    ]
    try:
        llm_response = chat_completion(base_prompt)
        checklist = json.loads(llm_response)
        if not isinstance(checklist, list) or not all(isinstance(item, dict) and 'label' in item and 'text' in item for item in checklist):
            raise ValueError("LLM did not return a valid checklist array")
        # Iterative refinement: up to 2 more passes
        for _ in range(2):
            refine_prompt = [
                {"role": "system", "content": (
                    "You are an expert at reading documents. Compare the following checklist to the document text. "
                    "If you find any items in the document that are not represented in the checklist, add them. "
                    "Return the improved checklist as a JSON array with 'label' and 'text' for each item. "
                    "Only output the JSON array, no explanation or markdown."
                )},
                {"role": "user", "content": f"Document text:\n{text[:12000]}\n\nChecklist so far:\n{json.dumps(checklist, ensure_ascii=False)}"}
            ]
            llm_response = chat_completion(refine_prompt)
            new_checklist = json.loads(llm_response)
            # If no change, break early
            if new_checklist == checklist:
                break
            checklist = new_checklist
        return {"checklist": checklist}
    except Exception as e:
        print(f"[ERROR] Checklist extraction failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to extract checklist: {str(e)}")
