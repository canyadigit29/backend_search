from fastapi import APIRouter, HTTPException, Query
from app.core.extract_text import extract_text
from app.core.supabase_client import supabase
import os
import tempfile

router = APIRouter()

@router.get("/extract_text")
async def api_extract_text(file_path: str = Query(...)):
    """
    Downloads a file from Supabase Storage, extracts text if PDF, and returns the text and file name.
    """
    try:
        # Download file from Supabase Storage
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "files")
        file_response = supabase.storage.from_(bucket).download(file_path)
        if not file_response:
            raise HTTPException(status_code=404, detail="File not found in storage.")
        file_bytes = file_response.read()
        # Save to a temp file
        ext = os.path.splitext(file_path)[-1] or ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
            tmp_file.write(file_bytes)
            tmp_file_path = tmp_file.name
        # Extract text
        text = extract_text(tmp_file_path)
        file_name = os.path.basename(file_path)
        # Clean up temp file
        os.remove(tmp_file_path)
        return {"text": text, "file_name": file_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract text: {str(e)}")
