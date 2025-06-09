from fastapi import APIRouter, HTTPException, Query
from app.core.extract_text import extract_text
from app.core.supabase_client import supabase
import os
import tempfile
import traceback

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
